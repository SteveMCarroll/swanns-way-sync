"""Build the Within a Budding Grove cross-medium correspondence (Moncrieff + audio only).

Pipeline:
  1. parse the Modern Library OCR epub into page records (epub_parse_bg)
  2. select ~1 landmark per ~10 audio-min, snapped to printed-page starts
  3. assign each landmark its exact physical page (Part I/II reset) and section
  4. assign audio time: fuzzy-match the page incipit against tools/transcript_bg.json
     (seeded, monotonic, windowed search), else constant-WPM fallback
  5. emit src/_data/budding_grove.json, correspondence_bg.csv,
     out/BuddingGrove_landmarks.cue, out/BuddingGrove_chapters.ffmetadata
"""
import csv
import json
import os
import re
import subprocess
from pathlib import Path

from epub_parse_bg import parse_budding_grove, INCIPIT_WORDS

ROOT = Path(__file__).resolve().parent.parent
TOOLS = Path(__file__).resolve().parent
TRANSCRIPT = TOOLS / "transcript_bg.json"
TRACK_STARTS = TOOLS / "_bg_track_starts.json"
DATA_OUT = ROOT / "src" / "_data" / "budding_grove.json"
CSV_OUT = ROOT / "correspondence_bg.csv"
OUT_DIR = ROOT / "out"
M4B = OUT_DIR / "Within a Budding Grove.m4b"

TARGET_SPACING_SEC = 600           # ~10 minutes between landmarks

# The gutenberg.net.au source epub is clean except for a handful of stray scan
# errors on proper nouns (e.g. "Combray" mis-scanned once as "Combfay", while the
# other 45 occurrences are correct). Correct known single-instance errors so the
# published incipit matches the printed book.
INCIPIT_FIXES = {
    "Combfay": "Combray",
}

SECTION_ORDER = [
    "Madame Swann at Home",
    "Place-Names: The Place",
]
SECTION_SHORT = {
    "Madame Swann at Home": "Madame Swann at Home",
    "Place-Names: The Place": "Place-Names",
}
PART_SHORT = {"Part I": "Pt I", "Part II": "Pt II"}

# Verified content gaps in THIS audiobook rip: the narration skips these printed
# ranges entirely (confirmed absent from the source recording - the reader finishes
# the page before the range and continues on the page after it with no pause; the
# transcript timestamps are continuous across the skip). Landmarks whose page falls
# inside a gap are dropped and replaced by a single labelled marker chapter placed
# where the audio resumes.
KNOWN_GAPS = [
    {"part": "Part II", "from_page": 481, "to_page": 500, "resume_page": 501,
     "resume_seconds": 62486.0,                       # 17:21:26
     "resume_section": "Place-Names: The Place"},
    {"part": "Part II", "from_page": 645, "to_page": 679, "resume_page": 680,
     "resume_seconds": 80994.0,                       # 22:29:54
     "resume_section": "Place-Names: The Place"},
]

# Curated scene hints (single proper nouns / plain words — not protected expression).
SCENE_HINTS = [
    ("Norpois", "M. de Norpois"),
    ("Bergotte", "Bergotte"),
    ("Berma", "La Berma"),
    ("Gilberte", "Gilberte"),
    ("Swann", "the Swanns"),
    ("Odette", "Odette"),
    ("Balbec", "Balbec"),
    ("Saint-Loup", "Saint-Loup"),
    ("Charlus", "M. de Charlus"),
    ("Bloch", "Bloch"),
    ("Villeparisis", "Mme de Villeparisis"),
    ("Rivebelle", "Rivebelle"),
    ("Elstir", "Elstir"),
    ("Albertine", "Albertine"),
    ("Andr\u00e9e", "Andr\u00e9e"),
]


def audio_duration():
    if TRANSCRIPT.exists():
        try:
            return float(json.loads(TRANSCRIPT.read_text(encoding="utf-8"))["duration"])
        except Exception:
            pass
    if M4B.exists():
        try:
            out = subprocess.check_output(
                ["ffprobe", "-v", "error", "-show_entries", "format=duration",
                 "-of", "csv=p=0", str(M4B)], text=True)
            return float(out.strip())
        except Exception:
            pass
    return 87735.4                  # sum of source disc durations (fallback)


def norm_words(text):
    return re.sub(r"[^a-z0-9 ]", " ", text.lower()).split()


def hhmmss(t):
    t = int(round(t))
    return f"{t//3600:d}:{(t%3600)//60:02d}:{t%60:02d}"


def select_landmarks(pages, duration):
    """Evenly spaced by word-fraction, snapped to page starts; skip unreliable pages."""
    total = sum(p.nwords for p in pages)
    n_target = max(1, int(round(duration / TARGET_SPACING_SEC)))
    step = total / n_target
    chosen, seen = [], set()
    for k in range(n_target + 1):
        w = int(k * step)
        cand = pages[0]
        for i, p in enumerate(pages):
            if p.word_start <= w:
                cand = p
                cand_i = i
            else:
                break
        # nudge off unreliable OCR pages to the next reliable page
        i = pages.index(cand)
        while not pages[i].reliable and i + 1 < len(pages):
            i += 1
        cand = pages[i]
        if cand.idx in seen:
            continue
        seen.add(cand.idx)
        chosen.append(cand)
    return chosen


def scene_hint(text, used):
    low = text.lower()
    for key, label in SCENE_HINTS:
        if key.lower() in low and label not in used:
            used.add(label)
            return label
    return ""


def _ends_sentence(raw):
    s = raw.rstrip('"\')]}\u201d\u2019 ')
    return s[-1:] in ".!?"


def _build_epub_stream(pages):
    """Flatten pages into a global word stream with per-word page/section metadata and
    sentence-start flags (a word is a sentence start iff the previous stream word ended
    with sentence punctuation). Global index aligns with Page.word_start offsets."""
    toks, norms, meta, sent_start = [], [], [], []
    prev_ended = True
    for p in pages:
        for raw in p.text.split():
            nw = norm_words(raw)
            if not nw:
                prev_ended = prev_ended or _ends_sentence(raw)
                continue
            toks.append(raw)
            norms.append(nw[0] if len(nw) == 1 else " ".join(nw))
            meta.append((p.part, p.section, p.page))
            sent_start.append(prev_ended)
            prev_ended = _ends_sentence(raw)
    return toks, norms, meta, sent_start


# Audible / disc-boundary boilerplate the narrator reads between discs. Pauses that
# open onto this text are NOT book paragraph breaks and must be excluded.
AD_MARKERS = (
    "audible", "audiobook", "this program", "hopes you have enjoyed",
    "has been broken", "end of disc", "end of part", "compact disc",
    "recorded books", "this is the end", "of this recording",
    "marcel proust", "remembrance of things past", "translated by",
)


def snap_to_paragraphs(rows, pages, window=200.0, pause=1.4):
    """Move each real landmark's break to the nearest audiobook paragraph boundary (a
    long narration pause that follows a sentence end) so chapters begin on a clean
    paragraph opening. The incipit is taken from the transcript at that point (Whisper
    capitalises sentence starts and reads the book verbatim), which is far cleaner than
    the garbled page OCR; the epub page is refined only when it stays consistent."""
    if not TRANSCRIPT.exists():
        return
    from rapidfuzz import fuzz
    data = json.loads(TRANSCRIPT.read_text(encoding="utf-8"))
    # transcript precise word stream (raw + normalized + start time)
    traw, tnorm, tt = [], [], []
    for s in data["segments"]:
        for wd in (s.get("words") or []):
            nw = norm_words(wd["w"])
            if not nw:
                continue
            traw.append(wd["w"].strip())
            tnorm.append(" ".join(nw))
            tt.append(float(wd["s"]))

    def is_ad(widx):
        ctx = " ".join(tnorm[max(0, widx - 2):widx + 10])
        return any(m in ctx for m in AD_MARKERS)

    def opens_clean(widx):
        w = traw[widx].lstrip('"\'([\u201c\u2018\u2014\u2013- ')
        return bool(w) and w[:1].isupper()

    # paragraph boundaries: long pause AND previous spoken word ends a sentence AND the
    # opening word is a capitalised sentence start AND it's book text (not Audible ads)
    paras = []
    for i in range(1, len(tt)):
        if (tt[i] - tt[i - 1] >= pause and _ends_sentence(traw[i - 1])
                and opens_clean(i) and not is_ad(i)):
            paras.append((tt[i], i))
    if not paras:
        return
    ptimes = [p[0] for p in paras]

    etok, enorm, emeta, esent = _build_epub_stream(pages)
    NE = len(enorm)

    def _is_start(i):
        if not (0 <= i < NE) or not esent[i]:
            return False
        w = etok[i].lstrip('"\'([\u201c\u2018\u2014\u2013- ')
        return bool(w) and w[:1].isupper()

    def clean_start(gi, reach=8):
        for d in range(0, reach + 1):
            for cand in (gi - d, gi + d):
                if _is_start(cand):
                    return cand
        return -1

    def find_epub(words, center, span=2600):
        lo = max(0, center - span)
        hi = min(NE - len(words), center + span)
        if hi <= lo or not words:
            return -1, -1.0
        target = " ".join(words)
        best, bi = -1.0, -1
        for i in range(lo, hi + 1):
            sc = fuzz.ratio(" ".join(enorm[i:i + len(words)]), target)
            if sc > best:
                best, bi = sc, i
        return bi, best

    def clean_incipit(widx):
        toks = traw[widx:widx + INCIPIT_WORDS]
        return " ".join(toks).strip()

    def epub_incipit(s0):
        s = " ".join(etok[s0:s0 + INCIPIT_WORDS]).strip()
        for bad, good in INCIPIT_FIXES.items():
            s = re.sub(rf"\b{re.escape(bad)}\b", good, s)
        return s

    import bisect
    used = set()
    for r in rows:
        if r.get("is_gap"):
            continue
        # nearest un-used paragraph boundary to the landmark's matched audio time
        k = bisect.bisect_left(ptimes, r["audio_seconds"])
        order = sorted(range(max(0, k - 10), min(len(paras), k + 10)),
                       key=lambda j: abs(paras[j][0] - r["audio_seconds"]))
        chosen = None
        for j in order:
            if j in used or abs(paras[j][0] - r["audio_seconds"]) > window:
                continue
            chosen = j
            break
        if chosen is None:
            continue
        used.add(chosen)
        t, widx = paras[chosen]
        r["audio_seconds"] = round(t, 1)
        r["ml_incipit"] = clean_incipit(widx)
        r["audio_method"] = "para/" + r["audio_method"]
        # refine the epub page from this paragraph, but only if it stays consistent with
        # the already-matched page (guards against mislocation near ads / gaps). When the
        # epub sentence-start aligns with the spoken opening word, publish the book's clean
        # wording (proper spelling of names) instead of the phonetic transcript incipit.
        follow = tnorm[widx:widx + 12]
        gi, sc = find_epub(follow, int(r["word_start"]), span=6000)
        if gi >= 0 and sc >= 70:
            s0 = clean_start(gi)
            if s0 >= 0:
                part, section, page = emeta[s0]
                if abs(page - r["page"]) <= 4 and part == r["part"]:
                    r["word_start"] = s0
                    r["page"] = page
                    r["section"] = section
                    r["section_short"] = SECTION_SHORT[section]
                if sc >= 84 and abs(s0 - gi) <= 2:
                    a = " ".join(enorm[s0:s0 + INCIPIT_WORDS])
                    c = " ".join(tnorm[widx:widx + INCIPIT_WORDS])
                    if fuzz.ratio(a, c) >= 62:      # same passage; leading name may differ
                        ci = epub_incipit(s0)
                        head = ci.lstrip('"\'([\u201c\u2018\u2014\u2013- ')
                        if head[:1].isupper() and 0 < len(ci.split()) <= INCIPIT_WORDS:
                            r["ml_incipit"] = ci
    rows.sort(key=lambda r: r["audio_seconds"])
    for i in range(1, len(rows)):
        if rows[i]["audio_seconds"] < rows[i - 1]["audio_seconds"]:
            rows[i]["audio_seconds"] = rows[i - 1]["audio_seconds"]


def gap_for(part, page):
    for g in KNOWN_GAPS:
        if part == g["part"] and g["from_page"] <= page <= g["to_page"]:
            return g
    return None


def apply_gaps(rows):
    """Drop landmarks that fall inside a verified missing-audio range and insert one
    marker chapter per gap at the point where the recording resumes."""
    kept = [r for r in rows if gap_for(r["part"], r["page"]) is None]
    for g in KNOWN_GAPS:
        pt = PART_SHORT[g["part"]]
        sect = g["resume_section"]
        kept.append({
            "part": g["part"],
            "section": sect,
            "section_short": SECTION_SHORT[sect],
            "page": g["resume_page"],
            "scene": "",
            "ml_incipit": "",
            "word_start": None,
            "audio_seconds": round(g["resume_seconds"], 1),
            "audio_time": "",
            "audio_track": track_for(g["resume_seconds"]),
            "audio_method": "gap",
            "is_gap": True,
            "gap_from": g["from_page"],
            "gap_to": g["to_page"],
            "label": (f"\u26a0 {pt} pp.{g['from_page']}\u2013{g['to_page']} not in "
                      f"this recording \u2014 audio resumes p.{g['resume_page']}"),
        })
    kept.sort(key=lambda r: r["audio_seconds"])
    return kept


def build():
    pages = parse_budding_grove()
    duration = audio_duration()
    total_words = sum(p.nwords for p in pages)
    landmarks = select_landmarks(pages, duration)

    rows = []
    used_hints = set()
    for lm in landmarks:
        hint = scene_hint(lm.text, used_hints)
        rows.append({
            "part": lm.part,
            "section": lm.section,
            "section_short": SECTION_SHORT[lm.section],
            "page": lm.page,
            "scene": hint,
            "ml_incipit": lm.incipit,
            "ml_match": lm.match,           # internal, for audio matching
            "word_start": lm.word_start,
            "audio_seconds": round(lm.word_start / total_words * duration, 1),
            "audio_time": "",
            "audio_track": 0,
            "audio_method": "estimate",
        })

    assign_audio(rows, duration)

    rows = apply_gaps(rows)
    snap_to_paragraphs(rows, pages)

    for i, r in enumerate(rows, 1):
        r["n"] = i
        r["audio_time"] = hhmmss(r["audio_seconds"])
        if not r.get("is_gap"):
            pt = PART_SHORT[r["part"]]
            label = f"{pt} \u00b7 p.{r['page']} \u2014 {r['section_short']}"
            if r["scene"]:
                label += f" ({r['scene']})"
            r["label"] = label
        r.pop("ml_match", None)             # never publish the longer match phrase

    write_outputs(rows, duration)
    return rows


def assign_audio(rows, duration):
    """Fuzzy-match incipits against transcript_bg.json if available; else WPM estimate."""
    if not TRANSCRIPT.exists():
        print("[audio] transcript_bg.json not found -> constant-WPM estimates")
        for r in rows:
            r["audio_track"] = track_for(r["audio_seconds"])
        return
    from rapidfuzz import fuzz
    data = json.loads(TRANSCRIPT.read_text(encoding="utf-8"))
    segs = data["segments"]
    # ROBUST space: one word per segment token, time interpolated within the segment.
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
    # PRECISION space: exact per-word times + sentence-start times.
    wl_tok, wl_t, sent_t = [], [], []
    prev_end_sentence = True
    for s in segs:
        for wd in (s.get("words") or []):
            raw = wd["w"].strip()
            for j, tok in enumerate(norm_words(wd["w"])):
                wl_tok.append(tok)
                wl_t.append(float(wd["s"]))
                if prev_end_sentence and j == 0:
                    sent_t.append(float(wd["s"]))
                    prev_end_sentence = False
            if raw and raw[-1] in ".!?":
                prev_end_sentence = True
    sent_t.sort()

    import bisect

    def refine_time(approx_t, phrase, win=30, thresh=60):
        if not wl_t or not phrase:
            return approx_t, False
        L = len(phrase)
        lo = bisect.bisect_left(wl_t, approx_t - win)
        hi = min(len(wl_tok) - L, bisect.bisect_left(wl_t, approx_t + win))
        target = " ".join(phrase)
        best, bi = -1, -1
        for i in range(lo, hi + 1):
            sc = fuzz.ratio(" ".join(wl_tok[i:i + L]), target)
            if sc > best:
                best, bi = sc, i
        if bi >= 0 and best >= thresh:
            return wl_t[bi], True
        return approx_t, False

    def snap_sentence(t):
        if not sent_t:
            return t
        k = bisect.bisect_right(sent_t, t + 0.05)
        if k > 0 and 0 <= t - sent_t[k - 1] <= 6:
            return sent_t[k - 1]
        return t

    def scan(phrase, lo, hi):
        """Best fuzzy position of phrase within transcript word-index [lo, hi]."""
        L = len(phrase)
        lo = max(0, lo)
        hi = min(N - L, hi)
        if hi < lo or L == 0:
            return -1.0, -1
        target = " ".join(phrase)
        best, bi = -1.0, -1
        for i in range(lo, hi + 1):
            sc = fuzz.ratio(" ".join(joined[i:i + L]), target)
            if sc > best:
                best, bi = sc, i
        return best, bi

    ACCEPT = 70          # record a time
    STRONG = 84          # trust as a re-anchor / bracket boundary
    HALF = 9000          # seeded window (~1h of audio each side)

    for r in rows:
        r["_idx"] = None

    # PASS 1 - seeded search; only STRONG hits become anchors (weak hits can't poison
    # the seed for everything downstream, which is what stranded the back half before).
    total_w = max(r["word_start"] for r in rows) + 1
    rate = N / total_w
    anchor_w, anchor_idx = 0, 0
    for r in rows:
        phrase = norm_words(r.get("ml_match") or r["ml_incipit"])
        seed = int(anchor_idx + (r["word_start"] - anchor_w) * rate)
        best, bi = scan(phrase, seed - HALF, seed + HALF)
        if best >= STRONG:
            t, _ = refine_time(times[bi], phrase)
            r["audio_seconds"] = round(t, 1)
            r["audio_method"] = f"matched({best:.0f})"
            r["_idx"] = bi
            anchor_w, anchor_idx = r["word_start"], bi

    # PASS 2 - fill each gap between consecutive strong anchors with a monotonic,
    # bracket-bounded global search (head and tail brackets included). Phrases are
    # distinctive enough that the true spot wins even over a wide range.
    strong = [i for i, r in enumerate(rows) if r["_idx"] is not None]
    brackets = []
    prev = -1
    prev_idx = 0
    for s in strong + [len(rows)]:
        brackets.append((prev, s, prev_idx, rows[s]["_idx"] if s < len(rows) else N))
        prev, prev_idx = s, (rows[s]["_idx"] if s < len(rows) else N)
    for a, b, lo_idx, hi_idx in brackets:
        cur = lo_idx
        for j in range(a + 1, b):
            r = rows[j]
            phrase = norm_words(r.get("ml_match") or r["ml_incipit"])
            best, bi = scan(phrase, cur, hi_idx)
            if best >= ACCEPT and bi >= 0:
                t, _ = refine_time(times[bi], phrase)
                r["audio_seconds"] = round(t, 1)
                r["audio_method"] = f"fill({best:.0f})"
                r["_idx"] = bi
                cur = bi

    # PASS 3 - anything still unplaced: linear interpolate between placed neighbours,
    # then snap back to a sentence start so it never lands mid-sentence.
    for idx, r in enumerate(rows):
        if r["_idx"] is not None:
            continue
        lo_i = next((j for j in range(idx - 1, -1, -1) if rows[j]["_idx"] is not None), None)
        hi_i = next((j for j in range(idx + 1, len(rows)) if rows[j]["_idx"] is not None), None)
        if lo_i is not None and hi_i is not None:
            w0, w1 = rows[lo_i]["word_start"], rows[hi_i]["word_start"]
            t0, t1 = rows[lo_i]["audio_seconds"], rows[hi_i]["audio_seconds"]
            frac = (r["word_start"] - w0) / (w1 - w0) if w1 > w0 else 0.5
            r["audio_seconds"] = round(snap_sentence(t0 + frac * (t1 - t0)), 1)
        r["audio_method"] += "/interp"

    # enforce monotonic non-decreasing times, then assign tracks
    for i in range(1, len(rows)):
        if rows[i]["audio_seconds"] < rows[i - 1]["audio_seconds"]:
            rows[i]["audio_seconds"] = rows[i - 1]["audio_seconds"]
    for r in rows:
        r["audio_track"] = track_for(r["audio_seconds"])
        r.pop("_idx", None)
    matched = sum(1 for r in rows if "matched" in r["audio_method"] or "fill" in r["audio_method"])
    print(f"[audio] placed {matched}/{len(rows)} landmarks by transcript match")


_TRACKS = None


def track_for(sec):
    global _TRACKS
    if _TRACKS is None:
        _TRACKS = json.loads(TRACK_STARTS.read_text()) if TRACK_STARTS.exists() else [0.0]
    n = 1
    for i, t in enumerate(_TRACKS):
        if sec >= t:
            n = i + 1
    return n


def chapter_title(r):
    return r["label"]


def write_outputs(rows, duration):
    DATA_OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT_DIR.mkdir(exist_ok=True)
    meta = {
        "title": "Within a Budding Grove",
        "audio_duration_seconds": round(duration, 1),
        "n_landmarks": len(rows),
        "editions": {
            "audio": "Moncrieff-derived audiobook (m4b)",
            "moncrieff": "Moncrieff / Modern Library (single-volume pagination)",
        },
        "audio_gaps": [
            {"part": g["part"], "from_page": g["from_page"], "to_page": g["to_page"],
             "resume_page": g["resume_page"], "resume_time": hhmmss(g["resume_seconds"])}
            for g in KNOWN_GAPS
        ],
    }
    DATA_OUT.write_text(json.dumps({"meta": meta, "landmarks": rows},
                                   ensure_ascii=False, indent=2), encoding="utf-8")

    cols = ["n", "part", "section", "page", "label", "ml_incipit",
            "audio_time", "audio_track", "audio_method"]
    with open(CSV_OUT, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=cols, extrasaction="ignore")
        w.writeheader()
        for r in rows:
            w.writerow(r)

    write_cue(rows)
    write_ffmetadata(rows, duration)
    print(f"[out] {len(rows)} landmarks -> {DATA_OUT.name}, {CSV_OUT.name}, "
          f"out/*.cue, out/*.ffmetadata")


def write_cue(rows):
    lines = ['FILE "Within a Budding Grove.m4b" MP3']
    for r in rows:
        t = r["audio_seconds"]
        mm, ss = int(t // 60), int(t % 60)
        ff = min(74, int(round((t - int(t)) * 75)))
        title = chapter_title(r).replace('"', "'")
        lines.append(f"  TRACK {r['n']:02d} AUDIO")
        lines.append(f'    TITLE "{title}"')
        lines.append(f"    INDEX 01 {mm}:{ss:02d}:{ff:02d}")
    (OUT_DIR / "BuddingGrove_landmarks.cue").write_text("\n".join(lines) + "\n",
                                                        encoding="utf-8")


def write_ffmetadata(rows, duration):
    out = [";FFMETADATA1", "title=Within a Budding Grove"]
    for i, r in enumerate(rows):
        start_ms = int(r["audio_seconds"] * 1000)
        end_ms = int(rows[i + 1]["audio_seconds"] * 1000) if i + 1 < len(rows) \
            else int(duration * 1000)
        if end_ms <= start_ms:
            end_ms = start_ms + 1000
        title = chapter_title(r).replace("\\", "\\\\").replace("=", "\\=") \
                                .replace(";", "\\;").replace("#", "\\#")
        out += ["[CHAPTER]", "TIMEBASE=1/1000",
                f"START={start_ms}", f"END={end_ms}", f"title={title}"]
    (OUT_DIR / "BuddingGrove_chapters.ffmetadata").write_text("\n".join(out) + "\n",
                                                              encoding="utf-8")


if __name__ == "__main__":
    rows = build()
    print("\nFirst 8 landmarks:")
    for r in rows[:8]:
        print(f"  {r['n']:3d} [{r['audio_time']}] {r['label']}  <{r['audio_method']}>")
        print(f"        p{r['page']}: {r['ml_incipit']}")
