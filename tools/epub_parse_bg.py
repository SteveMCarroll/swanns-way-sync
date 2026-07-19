"""Parse the Within a Budding Grove (Modern Library, 2-vol OCR) epub into page records.

The epub stores one printed page per file (page_N.html) as a single OCR'd <p>. The
printed page number maps linearly to the file index and *resets* at the Part I/II
volume boundary:

    Part I : page_15..411  -> printed = N-14   (Madame Swann at Home 1-306; Place-Names 307-397)
    Part II: page_413..768 -> printed = N-412  (Place-Names 1-120; Seascape 121-356)

No book text is emitted by callers beyond short (<=8 word) incipit locators; this module
just builds in-memory page records with word offsets, exact page numbers, and section.
"""
import html
import os
import re
from dataclasses import dataclass, field
from pathlib import Path

# Extract dir (gitignored, lives in the main checkout). Override with BG_EPUB_DIR.
_DEFAULT_EPUB = Path(
    r"D:\source\proust-audio-sync\_extract\Within_a_Budding_Grove_Modern_Library\EPUB"
)
EPUB_DIR = Path(os.environ.get("BG_EPUB_DIR", str(_DEFAULT_EPUB)))

FIRST_CONTENT = 15
LAST_CONTENT = 768
INCIPIT_WORDS = 7          # published locator cap (policy <=8)
MATCH_WORDS = 11           # longer phrase used only for audio matching (never published)


@dataclass
class Page:
    idx: int                 # page_N file number
    part: str                # "Part I" | "Part II"
    section: str             # narrative movement
    page: int                # printed page number (as in the physical book)
    text: str                # cleaned narrative text (running header + page no. stripped)
    word_start: int = 0
    nwords: int = 0
    reliable: bool = True
    incipit: str = ""        # first ~7 narrative words
    match: str = ""          # first ~11 narrative words (internal)


def classify(idx: int):
    """(part, section, printed_page) for a content page file index."""
    if idx <= 411:
        part = "Part I"
        page = idx - 14
        section = "Madame Swann at Home" if page <= 306 else "Place-Names: The Place"
    else:
        part = "Part II"
        page = idx - 412
        section = "Place-Names: The Place" if page <= 120 else "Seascape, with Frieze of Girls"
    return part, section, page


def _body_text(raw: str) -> str:
    m = re.search(r"<body[^>]*>(.*?)</body>", raw, re.S | re.I)
    inner = m.group(1) if m else raw
    inner = re.sub(r"(?s)<[^>]+>", " ", inner)
    return re.sub(r"\s+", " ", html.unescape(inner)).strip()


def _is_header_tok(tok: str) -> bool:
    """True for running-header noise: all-caps words (incl. OCR variants), single
    letters, bare digits, or punctuation-only tokens."""
    core = tok.strip(".,;:!?\"'()[]{}*-\u2014\u2019\u201c\u201d")
    if not core:
        return True
    if core.isdigit():
        return True
    letters = [c for c in core if c.isalpha()]
    if letters and all(c.isupper() for c in letters):
        return True
    return False


def _strip_header(text: str) -> str:
    """Drop the leading run of running-header / drop-cap tokens so the incipit starts
    on real narrative wording."""
    toks = text.split()
    i = 0
    while i < len(toks) and i < 14 and _is_header_tok(toks[i]):
        i += 1
    return " ".join(toks[i:])


def _strip_trailing_pageno(text: str) -> str:
    """Remove a trailing page-number token and short OCR noise at the page foot."""
    toks = text.split()
    while toks and _is_header_tok(toks[-1]):
        toks.pop()
    return " ".join(toks)


def parse_budding_grove() -> list[Page]:
    pages: list[Page] = []
    wc = 0
    for idx in range(FIRST_CONTENT, LAST_CONTENT + 1):
        f = EPUB_DIR / f"page_{idx}.html"
        if not f.exists():
            continue
        raw = f.read_text(encoding="utf-8", errors="replace")
        body = _body_text(raw)
        reliable = "estimated to be only" not in body.lower()
        clean = _strip_trailing_pageno(_strip_header(body))
        n = len(clean.split())
        if n == 0:
            continue
        part, section, page = classify(idx)
        words = clean.split()
        pg = Page(
            idx=idx, part=part, section=section, page=page, text=clean,
            word_start=wc, nwords=n, reliable=reliable,
            incipit=" ".join(words[:INCIPIT_WORDS]),
            match=" ".join(words[:MATCH_WORDS]),
        )
        pages.append(pg)
        wc += n
    return pages


if __name__ == "__main__":
    ps = parse_budding_grove()
    from collections import Counter
    words = Counter()
    pagerange = {}
    for p in ps:
        words[p.section] += p.nwords
        a, b = pagerange.get((p.part, p.section), (10**9, -1))
        pagerange[(p.part, p.section)] = (min(a, p.page), max(b, p.page))
    total = sum(p.nwords for p in ps)
    print(f"== {len(ps)} content pages, {total} words ==")
    for (part, sec), (a, b) in pagerange.items():
        print(f"  {part:7} {sec:32} pages {a}-{b}  ({words[sec]} words in section)")
    print("\nUnreliable pages:", [p.idx for p in ps if not p.reliable])
    print("\nBoundary samples:")
    for target in (15, 320, 321, 411, 413, 532, 533, 768):
        for p in ps:
            if p.idx == target:
                print(f"  page_{p.idx} [{p.part} p{p.page} {p.section}]: {p.incipit!r}")
                break
