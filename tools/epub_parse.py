"""Parse the two Swann's Way epubs into structured paragraph records.

No book text is emitted here beyond what callers choose to keep; this module
just builds in-memory paragraph lists with word offsets and (for Moncrieff)
print-page numbers from the embedded <a id="pageN"/> anchors.
"""
import html
import re
from dataclasses import dataclass, field
from html.parser import HTMLParser
from pathlib import Path

EXTRACT = Path(__file__).resolve().parent.parent / "_extract"
ML_DIR = EXTRACT / "Swanns_Way_Modern_Library" / "OEBPS"
DV_DIR = EXTRACT / "Swanns_Way_Penguin_Davis" / "OEBPS" / "Text"


@dataclass
class Para:
    part: str          # "Combray" | "Swann in Love" | "Place-Names"
    subpart: str       # finer bucket used for Davis page calibration
    text: str
    word_start: int = 0   # cumulative word index at start of this paragraph (within edition)
    nwords: int = 0
    page: int | None = None  # Moncrieff print page in force at paragraph start


class _Collector(HTMLParser):
    """Collects text for chosen block tags, tracking inline page anchors.

    keep(tag, attrs) -> bucket name (truthy) means "this element is a paragraph";
    the parser accumulates its inner text until the matching close tag.
    """

    def __init__(self, keep):
        super().__init__(convert_charrefs=True)
        self.keep = keep
        self.cur_page: int | None = None
        self.paras: list[tuple[str, str]] = []  # (bucket, text)
        self._stack: list[str | None] = []  # bucket names of open kept elements
        self._buf: list[str] = []

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        # Record page anchors: <a id="page123"/>
        if tag == "a" and a.get("id", "").startswith("page"):
            m = re.match(r"page(\d+)", a["id"])
            if m:
                self.cur_page = int(m.group(1))
        # Skip footnote superscript references entirely.
        bucket = self.keep(tag, a)
        if bucket:
            self._stack.append((bucket, self.cur_page))
            self._buf.append("\x00")  # marker for paragraph start in buffer

    def handle_startendtag(self, tag, attrs):
        # Self-closing tags like <a id="pageN"/> and <br/>
        self.handle_starttag(tag, attrs)

    def handle_endtag(self, tag):
        if self._stack and self._is_close_for(tag):
            bucket, page = self._stack.pop()
            text = "".join(self._buf).split("\x00")[-1]
            text = _clean(text)
            if text:
                self.paras.append((bucket, page, text))
            # trim buffer back
            self._buf = []

    # We don't track exact tag nesting for kept elements (they don't nest in
    # these epubs), so close on the relevant block tags only.
    _CLOSE_TAGS = {"p", "div"}

    def _is_close_for(self, tag):
        return tag in self._CLOSE_TAGS

    def handle_data(self, data):
        if self._stack:
            self._buf.append(data)


def _clean(t: str) -> str:
    t = html.unescape(t)
    t = re.sub(r"\s+", " ", t).strip()
    # strip a leading footnote digit cluster left by <sup> refs is not needed
    return t


def _words(t: str) -> int:
    return len(t.split())


# ---- Moncrieff (Modern Library) ----
def parse_moncrieff() -> list[Para]:
    files = [
        ("Prou_9780679641780_epub_p01_r1.htm", "Combray", "Combray-Overture"),
        ("Prou_9780679641780_epub_p01a_r1.htm", "Combray", "Combray-II"),
        ("Prou_9780679641780_epub_p02_r1.htm", "Swann in Love", "Swann"),
        ("Prou_9780679641780_epub_p02a_r1.htm", "Swann in Love", "Swann"),
        ("Prou_9780679641780_epub_p02b_r1.htm", "Swann in Love", "Swann"),
        ("Prou_9780679641780_epub_p03_r1.htm", "Place-Names", "Place-Names"),
    ]

    def keep(tag, a):
        if tag == "p":
            return "p"
        return None

    out: list[Para] = []
    wc = 0
    for fn, part, subpart in files:
        c = _Collector(keep)
        c.cur_page = None
        c.feed((ML_DIR / fn).read_text(encoding="utf-8", errors="replace"))
        for _bucket, page, text in c.paras:
            n = _words(text)
            if n == 0:
                continue
            out.append(Para(part, subpart, text, wc, n, page))
            wc += n
    # forward-fill pages just in case a paragraph started before first anchor
    last = None
    for p in out:
        if p.page is None:
            p.page = last
        else:
            last = p.page
    return out


# ---- Davis (Penguin) ----
def parse_davis() -> list[Para]:
    files = [
        ("prou_9781101501269_oeb_c01_r1.xhtml", "Combray", "Combray-Overture"),
        ("prou_9781101501269_oeb_c02_r1.xhtml", "Combray", "Combray-II"),
        ("prou_9781101501269_oeb_c02_r1_b.xhtml", "Combray", "Combray-II"),
        ("prou_9781101501269_oeb_p02_r1.xhtml", "Swann in Love", "Swann"),
        ("prou_9781101501269_oeb_p02_r1_b.xhtml", "Swann in Love", "Swann"),
        ("prou_9781101501269_oeb_p03_r1.xhtml", "Place-Names", "Place-Names"),
    ]

    def keep(tag, a):
        cls = a.get("class", "")
        if tag == "div" and cls.startswith("tx"):
            return "tx"
        return None

    out: list[Para] = []
    wc = 0
    for fn, part, subpart in files:
        c = _Collector(keep)
        c.feed((DV_DIR / fn).read_text(encoding="utf-8", errors="replace"))
        for _bucket, _page, text in c.paras:
            n = _words(text)
            if n == 0:
                continue
            out.append(Para(part, subpart, text, wc, n, None))
            wc += n
    return out


if __name__ == "__main__":
    ml = parse_moncrieff()
    dv = parse_davis()
    def summ(name, ps):
        from collections import Counter
        cw = Counter()
        for p in ps:
            cw[p.subpart] += p.nwords
        print(f"== {name}: {len(ps)} paragraphs, {sum(p.nwords for p in ps)} words ==")
        for k, v in cw.items():
            print(f"   {k:18} {v:>8} words")
    summ("Moncrieff", ml)
    summ("Davis", dv)
    print("\nMoncrieff page sanity (first paragraph page / last paragraph page):")
    print("  first:", ml[0].page, "| last:", ml[-1].page)
    pages = [p.page for p in ml if p.page]
    print("  min page:", min(pages), "max page:", max(pages))
    print("\nMoncrieff subpart -> page range:")
    from collections import defaultdict
    rng = defaultdict(lambda: [10**9, -1])
    for p in ml:
        if p.page:
            rng[p.subpart][0] = min(rng[p.subpart][0], p.page)
            rng[p.subpart][1] = max(rng[p.subpart][1], p.page)
    for k, v in rng.items():
        print(f"   {k:18} pages {v[0]}-{v[1]}")
    print("\nSample Moncrieff incipits:")
    for p in ml[:2]:
        print("   ", p.page, "|", " ".join(p.text.split()[:8]))
    print("Sample Davis incipits:")
    for p in dv[:2]:
        print("   ", " ".join(p.text.split()[:8]))
