"""Build the Swann's Way cross-medium correspondence.

Pipeline:
  1. parse both translations (epub_parse)
  2. select ~128 landmarks at Moncrieff paragraph starts spaced ~10 audio-min apart
  3. map each landmark Moncrieff paragraph -> Davis paragraph (word-fraction within Part)
  4. assign Moncrieff page (exact) + Davis physical/Kindle pages (per-subpart interpolation)
  5. assign audio time: fuzzy-match incipit against tools/transcript.json if present,
     else constant-WPM fallback
  6. emit src/_data/landmarks.json, correspondence.csv,
     out/Swanns_Way_landmarks.cue, out/Swanns_Way_chapters.ffmetadata
"""
import csv
import json
import os
import re
from pathlib import Path

from epub_parse import parse_moncrieff, parse_davis

ROOT = Path(__file__).resolve().parent.parent
TOOLS = Path(__file__).resolve().parent
TRANSCRIPT = TOOLS / "transcript.json"
DATA_OUT = ROOT / "src" / "_data" / "landmarks.json"
CSV_OUT = ROOT / "correspondence.csv"
OUT_DIR = ROOT / "out"

AUDIO_DURATION = 77247.44          # seconds (ffprobe)
INCIPIT_WORDS = 7                  # cap (policy: ~6-8 words)
TARGET_SPACING_SEC = 600           # ~10 minutes

# Davis per-subpart page calibration: physical (Penguin Classics) + Kindle (Deluxe)
DAVIS_PAGES = {
    "Combray-Overture": {"phys": (3, 48),  "kindle": (29, 80)},
    "Combray-II":       {"phys": (49, 192), "kindle": (81, 244)},
    "Swann":            {"phys": (193, 396), "kindle": (245, 475)},
    "Place-Names":      {"phys": (397, 444), "kindle": (476, 527)},
}

# The Moncrieff epub carries embedded print-page anchors from an older edition whose
# body starts at "page 1" (~603pp of novel text). The user reads the Modern Library
# *Kindle* edition, whose ToC pages differ (front matter counts; text is denser):
#   Combray 27, Swann in Love 257, Place-Names 501, Notes 557.
# Remap the epub anchor page onto the Kindle pagination, piecewise-linear per Part,
# anchored on the epub's own part-boundary pages.
#   part: (epub_start, epub_next_start, kindle_start, kindle_next_start)
ML_KINDLE = {
    "Combray":       (1,   265, 27,  257),
    "Swann in Love": (265, 545, 257, 501),
    "Place-Names":   (545, 603, 501, 557),
}


def ml_kindle_page(part, epub_page):
    """Convert an epub print-anchor page to the Modern Library Kindle page."""
    if epub_page is None or part not in ML_KINDLE:
        return epub_page
    e0, e1, k0, k1 = ML_KINDLE[part]
    if e1 == e0:
        return k0
    frac = (epub_page - e0) / (e1 - e0)
    frac = max(0.0, min(1.0, frac))
    return int(round(k0 + frac * (k1 - k0)))


PART_ORDER = ["Combray", "Swann in Love", "Place-Names"]
PART_SHORT = {"Combray": "Combray", "Swann in Love": "Swann", "Place-Names": "Place-Names"}

# Common capitalised words that are NOT proper-noun anchors.
STOP_CAPS = {
    "the", "a", "an", "and", "but", "or", "for", "nor", "so", "yet", "as", "at",
    "by", "in", "of", "on", "to", "up", "if", "it", "he", "she", "we", "you",
    "they", "his", "her", "my", "our", "their", "i", "this", "that", "these",
    "those", "then", "there", "here", "when", "while", "who", "what", "which",
    "how", "why", "where", "with", "from", "not", "no", "yes", "all", "one",
    "now", "out", "oh", "ah", "well", "even", "since", "after", "before",
    "alas", "indeed", "perhaps", "sometimes", "anyone", "would", "had", "was",
    "monsieur", "madame", "mademoiselle", "mme", "mlle", "saint",
}


def anchor_set(text):
    """Proper-noun-ish tokens: capitalised words whose lowercase isn't a stopword."""
    toks = re.findall(r"[A-Z][A-Za-z\u00c0-\u017f'\-]{2,}", text)
    return {t.lower().strip("'-") for t in toks
            if t.lower().strip("'-") not in STOP_CAPS}

# Curated scene hints (single proper nouns / plain words — not protected expression).
SCENE_HINTS = [
    ("madeleine", "the madeleine"),
    ("magic lantern", "the magic lantern"),
    ("Legrandin", "Legrandin"),
    ("hawthorn", "the hawthorns"),
    ("Vinteuil", "Vinteuil"),
    ("Montjouvain", "Montjouvain"),
    ("Martinville", "the steeples of Martinville"),
    ("Guermantes", "the Guermantes way"),
    ("Verdurin", "the Verdurins"),
    ("Odette", "Odette"),
    ("cattleya", "cattleyas"),
    ("Forcheville", "Forcheville"),
    ("Vinteuil", "Vinteuil's sonata"),
    ("Gilberte", "Gilberte"),
    ("Champs-Elysees", "the Champs-Elysees"),
    ("Bois", "the Bois de Boulogne"),
]


def norm_words(text):
    return re.sub(r"[^a-z0-9 ]", " ", text.lower()).split()


def incipit(text, n=INCIPIT_WORDS):
    return " ".join(text.split()[:n])


def part_ranges(paras):
    """word [start,end) per Part."""
    r = {}
    for p in paras:
        a, b = r.get(p.part, (10**9, -1))
        r[p.part] = (min(a, p.word_start), max(b, p.word_start + p.nwords))
    return r


def subpart_ranges(paras):
    r = {}
    for p in paras:
        a, b = r.get(p.subpart, (10**9, -1))
        r[p.subpart] = (min(a, p.word_start), max(b, p.word_start + p.nwords))
    return r


def find_para_at_word(paras_part, w):
    """First paragraph in a part-filtered list whose span contains word w (or nearest)."""
    best = paras_part[0]
    for p in paras_part:
        if p.word_start <= w < p.word_start + p.nwords:
            return p
        if p.word_start <= w:
            best = p
    return best


def refine_davis(lm, dlist, prop_idx, min_idx, name_df, anchor_cache, window=6):
    """Within +/-window of the proportional index, pick the Davis paragraph that best
    shares proper-noun anchors with the Moncrieff landmark; else the proportional one."""
    lm_names = anchor_set(lm.text)
    lo = max(min_idx, prop_idx - window)
    hi = min(len(dlist) - 1, prop_idx + window)
    base = dlist[max(min_idx, min(prop_idx, len(dlist) - 1))]
    if not lm_names:
        return base
    best_score, best_p = 0.0, None
    for i in range(lo, hi + 1):
        p = dlist[i]
        shared = lm_names & anchor_cache[id(p)]
        if not shared:
            continue
        score = sum(1.0 / (name_df.get(nm, 1) ** 0.5) for nm in shared)
        score -= 0.02 * abs(i - prop_idx)  # mild preference for proportional spot
        if score > best_score:
            best_score, best_p = score, p
    return best_p if best_p is not None else base


def interp_page(word_start, unit_rng, page_rng):
    a, b = unit_rng
    p0, p1 = page_rng
    if b <= a:
        return p0
    f = max(0.0, min(1.0, (word_start - a) / (b - a)))
    return int(round(p0 + f * (p1 - p0)))


def hhmmss(t):
    t = int(round(t))
    return f"{t//3600:d}:{(t%3600)//60:02d}:{t%60:02d}"


def select_landmarks(ml):
    total = sum(p.nwords for p in ml)
    # spacing in words ~ proportional to 10 audio-min under constant WPM
    n_target = max(1, int(round(AUDIO_DURATION / TARGET_SPACING_SEC)))
    step = total / n_target
    chosen = []
    seen = set()
    for k in range(n_target + 1):
        w = int(k * step)
        # nearest paragraph start at/after w within whole book
        cand = None
        for p in ml:
            if p.word_start >= w:
                cand = p
                break
            cand = p
        idx = ml.index(cand)
        if idx in seen:
            continue
        seen.add(idx)
        chosen.append(cand)
    return chosen


def scene_hint(text, used):
    low = text.lower()
    for key, label in SCENE_HINTS:
        if key.lower() in low and label not in used:
            used.add(label)
            return label
    return ""


def build():
    ml = parse_moncrieff()
    dv = parse_davis()
    total_ml = sum(p.nwords for p in ml)

    ml_part = part_ranges(ml)
    dv_part = part_ranges(dv)
    dv_sub = subpart_ranges(dv)
    dv_by_part = {pt: [p for p in dv if p.part == pt] for pt in PART_ORDER}

    # name anchor weighting: rarer shared names score higher
    from collections import Counter
    dv_name_df = Counter()
    for p in dv:
        for nm in anchor_set(p.text):
            dv_name_df[nm] += 1
    dv_anchor = {id(p): anchor_set(p.text) for p in dv}

    landmarks = select_landmarks(ml)

    rows = []
    used_hints = set()
    per_part_counter = {pt: 0 for pt in PART_ORDER}
    last_dv_idx = {pt: 0 for pt in PART_ORDER}
    for lm in landmarks:
        pt = lm.part
        per_part_counter[pt] += 1
        # Davis paragraph via word-fraction within Part, then local name-anchored snap
        a_ml, b_ml = ml_part[pt]
        f = (lm.word_start - a_ml) / max(1, (b_ml - a_ml))
        a_dv, b_dv = dv_part[pt]
        target_dv_word = a_dv + f * (b_dv - a_dv)
        dlist = dv_by_part[pt]
        prop_idx = 0
        for i, p in enumerate(dlist):
            if p.word_start <= target_dv_word:
                prop_idx = i
            else:
                break
        dpar = refine_davis(lm, dlist, prop_idx, last_dv_idx[pt],
                            dv_name_df, dv_anchor)
        last_dv_idx[pt] = dlist.index(dpar)
        # Davis pages
        sp = dpar.subpart
        phys = interp_page(dpar.word_start, dv_sub[sp], DAVIS_PAGES[sp]["phys"])
        kindle = interp_page(dpar.word_start, dv_sub[sp], DAVIS_PAGES[sp]["kindle"])

        hint = scene_hint(lm.text, used_hints)
        label = f"{PART_SHORT[pt]} \u00b7 {per_part_counter[pt]}"
        if hint:
            label += f" \u2014 {hint}"

        rows.append({
            "part": pt,
            "label": label,
            "scene": hint,
            "ml_incipit": incipit(lm.text),
            "ml_match": " ".join(lm.text.split()[:12]),  # internal, for audio matching
            "ml_page": ml_kindle_page(pt, lm.page),
            "dv_incipit": incipit(dpar.text),
            "dv_phys_page": phys,
            "dv_kindle_page": kindle,
            "ml_word_start": lm.word_start,
            # audio filled below
            "audio_seconds": round(lm.word_start / total_ml * AUDIO_DURATION, 1),
            "audio_time": "",
            "audio_track": 0,
            "audio_method": "estimate",
        })

    assign_audio(rows)
    for i, r in enumerate(rows, 1):
        r["n"] = i
        r["audio_time"] = hhmmss(r["audio_seconds"])
        r.pop("ml_match", None)  # don't publish the longer match phrase

    write_outputs(rows)
    return rows


def assign_audio(rows):
    """Fuzzy-match incipits against transcript.json if available; else keep WPM estimate."""
    if not TRANSCRIPT.exists():
        print("[audio] transcript.json not found -> using constant-WPM estimates")
        for r in rows:
            r["audio_track"] = track_for(r["audio_seconds"])
        return
    from rapidfuzz import fuzz
    data = json.loads(TRANSCRIPT.read_text(encoding="utf-8"))
    segs = data["segments"]
    # continuous word array with interpolated times
    words, times = [], []
    for s in segs:
        ws = norm_words(s["text"])
        if not ws:
            continue
        for i, w in enumerate(ws):
            words.append(w)
            times.append(s["start"] + (i + 0.5) / len(ws) * (s["end"] - s["start"]))
    joined = words
    N = len(joined)
    total_ml = max(r["ml_word_start"] for r in rows) + 1
    rate = N / total_ml          # transcript words per moncrieff word (~1.09)
    anchor_ml, anchor_idx = 0, 0  # last confirmed (ml_word, transcript_idx)
    last_idx = 0
    matched = 0
    for r in rows:
        phrase = norm_words(r.get("ml_match") or r["ml_incipit"])
        L = len(phrase)
        if L == 0:
            continue
        # seed locked to last confirmed match, projected by local rate
        seed = int(anchor_idx + (r["ml_word_start"] - anchor_ml) * rate)
        lo = max(last_idx, seed - 4000)
        hi = min(N - L, seed + 4000)
        if hi <= lo:
            lo, hi = last_idx, min(N - L, last_idx + 8000)
        target = " ".join(phrase)
        best_score, best_i = -1, -1
        i = lo
        while i <= hi:
            cand = " ".join(joined[i:i + L])
            sc = fuzz.ratio(cand, target)
            if sc > best_score:
                best_score, best_i = sc, i
            i += 1
        if best_score >= 68 and best_i >= 0:
            r["audio_seconds"] = round(times[best_i], 1)
            r["audio_method"] = f"matched({best_score:.0f})"
            last_idx = best_i
            anchor_ml, anchor_idx = r["ml_word_start"], best_i  # recalibrate
            matched += 1
        else:
            r["audio_method"] = f"estimate(miss {best_score:.0f})"
        r["audio_track"] = track_for(r["audio_seconds"])
    # interpolate unmatched landmarks between matched neighbors (by ml word position)
    for idx, r in enumerate(rows):
        if "matched" in r["audio_method"]:
            continue
        lo_i = next((j for j in range(idx - 1, -1, -1) if "matched" in rows[j]["audio_method"]), None)
        hi_i = next((j for j in range(idx + 1, len(rows)) if "matched" in rows[j]["audio_method"]), None)
        if lo_i is not None and hi_i is not None:
            w0, w1 = rows[lo_i]["ml_word_start"], rows[hi_i]["ml_word_start"]
            t0, t1 = rows[lo_i]["audio_seconds"], rows[hi_i]["audio_seconds"]
            frac = (r["ml_word_start"] - w0) / (w1 - w0) if w1 > w0 else 0.5
            r["audio_seconds"] = round(t0 + frac * (t1 - t0), 1)
            r["audio_method"] += "/interp"
            r["audio_track"] = track_for(r["audio_seconds"])
    for i in range(1, len(rows)):
        if rows[i]["audio_seconds"] < rows[i - 1]["audio_seconds"]:
            rows[i]["audio_seconds"] = rows[i - 1]["audio_seconds"]
            rows[i]["audio_time"] = ""
    print(f"[audio] matched {matched}/{len(rows)} landmarks to transcript")


_TRACKS = None


def track_for(sec):
    global _TRACKS
    if _TRACKS is None:
        p = TOOLS / "_track_starts.json"
        _TRACKS = json.loads(p.read_text()) if p.exists() else [0.0]
    n = 1
    for i, t in enumerate(_TRACKS):
        if sec >= t:
            n = i + 1
    return n


def write_outputs(rows):
    DATA_OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT_DIR.mkdir(exist_ok=True)
    meta = {
        "title": "Swann's Way",
        "audio_duration_seconds": AUDIO_DURATION,
        "n_landmarks": len(rows),
        "editions": {
            "audio": "Moncrieff-derived audiobook (m4b, 21h27m)",
            "davis": "Lydia Davis (Penguin)",
            "moncrieff": "Moncrieff / Modern Library",
        },
    }
    DATA_OUT.write_text(json.dumps({"meta": meta, "landmarks": rows},
                                   ensure_ascii=False, indent=2), encoding="utf-8")

    cols = ["n", "part", "label", "ml_incipit", "ml_page", "dv_incipit",
            "dv_phys_page", "dv_kindle_page", "audio_time", "audio_track",
            "audio_method"]
    with open(CSV_OUT, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=cols, extrasaction="ignore")
        w.writeheader()
        for r in rows:
            w.writerow(r)

    write_cue(rows)
    write_ffmetadata(rows)
    print(f"[out] {len(rows)} landmarks -> {DATA_OUT.name}, {CSV_OUT.name}, "
          f"out/*.cue, out/*.ffmetadata")


def chapter_title(r):
    """Landmark label with page numbers so you can switch mediums from the chapter list."""
    return (f"{r['label']} \u2014 Davis p{r['dv_phys_page']} "
            f"(K p{r['dv_kindle_page']}) \u00b7 ML p{r['ml_page']}")


def write_cue(rows):
    lines = ['FILE "Swann\'s Way.m4b" MP3']
    for r in rows:
        t = r["audio_seconds"]
        mm = int(t // 60)
        ss = int(t % 60)
        ff = int(round((t - int(t)) * 75))
        if ff >= 75:
            ff = 74
        title = chapter_title(r).replace('"', "'")
        lines.append(f"  TRACK {r['n']:02d} AUDIO")
        lines.append(f'    TITLE "{title}"')
        lines.append(f"    INDEX 01 {mm}:{ss:02d}:{ff:02d}")
    (OUT_DIR / "Swanns_Way_landmarks.cue").write_text("\n".join(lines) + "\n",
                                                      encoding="utf-8")


def write_ffmetadata(rows):
    out = [";FFMETADATA1", "title=Swann's Way"]
    for i, r in enumerate(rows):
        start_ms = int(r["audio_seconds"] * 1000)
        end_ms = int(rows[i + 1]["audio_seconds"] * 1000) if i + 1 < len(rows) \
            else int(AUDIO_DURATION * 1000)
        if end_ms <= start_ms:
            end_ms = start_ms + 1000
        title = chapter_title(r).replace("\\", "\\\\").replace("=", "\\=") \
                                .replace(";", "\\;").replace("#", "\\#")
        out += ["[CHAPTER]", "TIMEBASE=1/1000",
                f"START={start_ms}", f"END={end_ms}",
                f"title={title}"]
    (OUT_DIR / "Swanns_Way_chapters.ffmetadata").write_text("\n".join(out) + "\n",
                                                            encoding="utf-8")


if __name__ == "__main__":
    rows = build()
    print(f"\nFirst 6 landmarks:")
    for r in rows[:6]:
        print(f"  {r['n']:3d} [{r['audio_time']}] {r['label']}")
        print(f"        ML p{r['ml_page']}: {r['ml_incipit']}")
        print(f"        DV p{r['dv_phys_page']}/{r['dv_kindle_page']}: {r['dv_incipit']}")
