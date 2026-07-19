"""Parse the Within a Budding Grove (Modern Library / Moncrieff-Kilmartin-Enright)
epub into *paragraph* records with interpolated physical page numbers.

This is a clean digital text (proper spelling) wrapped by a feedbooks.com
distribution. The <p> tags are NOT paragraphs -- the source chops the running text
into arbitrary mid-sentence chunks, and real vs. arbitrary <p> breaks are
indistinguishable in the markup. So this epub carries NO recoverable paragraph
structure. That is fine: the actual sync-point paragraph boundaries come from the
audiobook narration pauses (see snap_to_paragraphs in build_correspondence_bg); this
module's job is only to provide a clean, ordered WORD STREAM with correct per-word
part / section / physical-page metadata for page interpolation and incipit locating.

We drop the injected boilerplate (running headers "Within A Budding Grove", bare page
numbers, "www.feedbooks.com" footers, front/back matter) and emit one record per
surviving text chunk. Records are only carriers of the word stream + page metadata;
their boundaries are not meaningful paragraph breaks.

The epub's own page anchors (p1..p339, ~700 words/page) are a *different* edition's
pagination and are ignored. Physical pages follow the user's paperback TOC:

    Part One  MADAME SWANN AT HOME       -> p.1
    Part Two  PLACE-NAMES: THE PLACE     -> p.299
    (Notes)                              -> p.733   (text ends ~732)

Pages are interpolated per part by word fraction (paragraphs are the reliable
anchor; the exact page can drift by a page or two, which the paragraph guide makes
survivable). No book text is published beyond short (<=8-word) incipit locators.
"""
import html
import os
import re
import zipfile
from dataclasses import dataclass
from pathlib import Path

_DEFAULT_EPUB = Path(
    r"D:\source\proust-audio-sync\Within_a_Budding_Grove_Moncrieff_Enright.epub"
)
EPUB_PATH = Path(os.environ.get("BG_EPUB", str(_DEFAULT_EPUB)))

SPLIT_FILES = [f"index_split_{i:03d}.html" for i in range(8)]

INCIPIT_WORDS = 7          # published locator cap (policy <=8)
MATCH_WORDS = 12           # longer phrase used only for audio matching (never published)

# Physical-page anchors from the paperback table of contents.
P1_PAGE = 1                # Part One first page
P2_PAGE = 299              # Part Two first page
END_PAGE = 733             # Notes begin here; text runs 1..732

# Section opening sentences (used to locate part/section starts by content).
# The paperback TOC has exactly two parts, so Part Two is uniformly Place-Names.
PART_ONE_OPEN = "My mother, when it was a question of our having"
PART_TWO_OPEN = "I had arrived at a state almost of complete indifference to Gilberte"

# Running-header / boilerplate paragraphs to drop (normalised, lower-cased).
_HEADERS = {
    "within a budding grove", "madame swann at home", "place-names: the place",
    "place-names the place", "seascape, with frieze of girls",
    "remembrance of things past", "marcel proust", "proust, marcel",
    "part i", "part ii", "part one", "part two", "www.feedbooks.com",
}


@dataclass
class Page:
    idx: int                 # chunk sequence number within the novel
    part: str                # "Part I" | "Part II"
    section: str             # narrative movement
    page: int                # interpolated physical page (paperback)
    text: str                # chunk text (arbitrary boundary, not a paragraph)
    word_start: int = 0
    nwords: int = 0
    reliable: bool = True
    incipit: str = ""        # first ~7 words (seed only; final incipits come from audio)
    match: str = ""          # first ~12 words (internal, for audio matching)


def _chunks() -> list[str]:
    """Return the ordered clean text chunks, dropping injected boilerplate.

    <p> boundaries are arbitrary; we keep every text chunk (stripped of the feedbooks
    footer) and drop running headers, bare page numbers and all-caps section titles."""
    out: list[str] = []
    z = zipfile.ZipFile(EPUB_PATH)
    for name in SPLIT_FILES:
        raw = z.read(name).decode("utf-8", "replace")
        body = re.search(r"<body[^>]*>(.*?)</body>", raw, re.S | re.I)
        inner = body.group(1) if body else raw
        for m in re.finditer(r"<p\b[^>]*>(.*?)</p>", inner, re.S | re.I):
            txt = html.unescape(re.sub(r"(?s)<[^>]+>", " ", m.group(1)))
            txt = re.sub(r"\s+", " ", txt).strip()
            txt = re.sub(r"\s*www\.feedbooks\.com\s*", " ", txt, flags=re.I).strip()
            low = txt.lower()
            is_pagenum = bool(re.fullmatch(r"\d+", txt))
            is_allcaps = (len(txt) < 60 and any(c.isalpha() for c in txt)
                          and txt == txt.upper())
            if not txt or low in _HEADERS or is_pagenum or is_allcaps:
                continue
            out.append(txt)
    return out


def _find(chunks, prefix, start=0):
    for i in range(start, len(chunks)):
        if chunks[i].startswith(prefix):
            return i
    return -1


def parse_budding_grove() -> list[Page]:
    chunks = _chunks()

    p1 = _find(chunks, PART_ONE_OPEN)
    p2 = _find(chunks, PART_TWO_OPEN, p1 + 1)
    if p1 < 0 or p2 < 0:
        raise RuntimeError("could not locate Part One/Two openings in epub")

    # novel end = first feedbooks back-matter chunk after Part Two
    end = len(chunks)
    for i in range(p2 + 1, len(chunks)):
        low = chunks[i].lower()
        if low.startswith(("loved this book", "\u2022")) or "similar users also downloaded" in low:
            end = i
            break

    body = chunks[p1:end]
    r_p2 = p2 - p1                            # relative Part Two start within `body`

    # word offsets per chunk and Part boundary word span (for page interpolation)
    offsets, wc = [], 0
    for chunk in body:
        offsets.append(wc)
        wc += len(chunk.split())
    total = wc
    w_p2 = offsets[r_p2]                      # words at Part Two start

    def page_for(w):
        if w < w_p2:                          # Part One: pages [1, 299)
            frac = w / w_p2 if w_p2 else 0.0
            return max(P1_PAGE, min(P2_PAGE - 1, int(P1_PAGE + frac * (P2_PAGE - P1_PAGE))))
        frac = (w - w_p2) / (total - w_p2) if total > w_p2 else 0.0
        return max(P2_PAGE, min(END_PAGE - 1, int(P2_PAGE + frac * (END_PAGE - P2_PAGE))))

    out: list[Page] = []
    for i, chunk in enumerate(body):
        if i < r_p2:
            part, section = "Part I", "Madame Swann at Home"
        else:
            part, section = "Part II", "Place-Names: The Place"
        words = chunk.split()
        out.append(Page(
            idx=i, part=part, section=section, page=page_for(offsets[i]),
            text=chunk, word_start=offsets[i], nwords=len(words), reliable=True,
            incipit=" ".join(words[:INCIPIT_WORDS]),
            match=" ".join(words[:MATCH_WORDS]),
        ))
    return out


if __name__ == "__main__":
    ps = parse_budding_grove()
    from collections import Counter
    words = Counter()
    prange = {}
    for p in ps:
        words[p.section] += p.nwords
        a, b = prange.get(p.section, (10**9, -1))
        prange[p.section] = (min(a, p.page), max(b, p.page))
    total = sum(p.nwords for p in ps)
    print(f"== {len(ps)} chunks, {total} words ==")
    for sec in ["Madame Swann at Home", "Place-Names: The Place"]:
        if sec in prange:
            a, b = prange[sec]
            print(f"  {sec:34} pages {a}-{b}  ({words[sec]} words)")
    print("\nBoundary samples:")
    seen2 = [p for p in ps if p.page == P2_PAGE]
    picks = ps[:1] + (seen2[:1] if seen2 else []) + ps[-1:]
    for p in picks:
        print(f"  [{p.part} p{p.page} {p.section}] {p.incipit!r}")
    print("\nFirst 6 incipits:")
    for p in ps[:6]:
        print(f"  p{p.page:>3}: {p.incipit}")
