"""FAA AC 43.13-1B (w/ Change 1) PDF -> chunk JSONL (SPEC §4.3).

    python parsers/faa_pdf.py [pdf] [-o build/faa_chunks.jsonl]

How it works, in order:
1. Read every text line with its font, size and position (PyMuPDF). Skip the
   front matter, the 9 duplicate pages and the header/footer strips.
2. Sort lines into kinds by font: body text is 12 pt Times, headings are Arial
   Bold CHAPTER/SECTION/APPENDIX lines, captions are bold "TABLE n-n."/
   "FIGURE n-n.", and everything else (8-9 pt Arial) belongs to a table or figure.
3. Put body lines in reading order: left column then right, in bands split by
   anything that spans both columns.
4. Walk the body lines: a bold "n-n." starts a paragraph, a bold WARNING/
   CAUTION/NOTE starts a warning chunk inside it. Paragraphs over 800 words
   are split at their bold "a." / "b." labels.
5. Tables (rows of lines grouped by position; pdfplumber's ruled-cell grids
   dropped headers and whole columns on this PDF) and figures (caption plus any
   label text) become their own chunks.
6. Pull refs_out and specs by regex, add hand fixes from ref_overrides.yaml.

Research behind each rule: research/faa-pdf-chunking.md in the wayfinder repo.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path

import pymupdf
import yaml

ROOT = Path(__file__).resolve().parent.parent
PDF = ROOT / "corpus/faa/AC_43.13-1B_w-chg1.pdf"
OVERRIDES = ROOT / "corpus/faa/ref_overrides.yaml"
OUT = ROOT / "build/faa_chunks.jsonl"

DOC = "AC 43.13-1B w/ Change 1 (FAA, 9/27/01)"
CITE = "AC 43.13-1B"
BODY_START = 35                   # first PDF page of chapter 1
DUP_PAGES = set(range(67, 76))    # PDF pp 67-75 repeat pp 58-66
HEADER_Y, FOOTER_Y = 64, 737      # header/footer strips (points from top)
SPLIT_WORDS = 800                 # split longer paragraphs at "a." / "b." labels

DASHES = {"\uf8e7": "–", "\uffe7": "–"}   # private-use range dashes in this PDF


def clean(s: str) -> str:
    for bad, good in DASHES.items():
        s = s.replace(bad, good)
    return s


def join_lines(lines: list[str]) -> str:
    """Join wrapped lines, undoing soft-hyphen line breaks."""
    out = ""
    for ln in lines:
        ln = ln.strip()
        if not ln:
            continue
        if out.endswith("\u00ad"):
            out = out[:-1] + ln
        elif out.endswith("-") and ln[:1].islower():
            out += ln
        else:
            out = f"{out} {ln}" if out else ln
    out = re.sub(r"-{4,}", " ", out.replace("\u00ad", ""))   # dot-leader dashes
    return re.sub(r"\s+", " ", out).strip()


# ---------------------------------------------------------------- lines

@dataclass
class Line:
    page: int                     # 1-based PDF page
    x0: float
    y0: float
    x1: float
    y1: float
    text: str
    size: float
    font: str
    bold: bool                    # first non-blank span is bold
    bold_lead: str                # text of the leading bold spans
    kind: str = ""                # body | heading | caption | aux
    col: int = 0


def is_bold(span) -> bool:
    return bool(span["flags"] & 16) or "Bold" in span["font"]


def page_lines(page, pno: int) -> tuple[list[Line], dict]:
    lines, meta = [], {"label": None, "chg1": False}
    for block in page.get_text("dict")["blocks"]:
        for l in block.get("lines", []):
            spans = [s for s in l["spans"] if s["text"].strip()]
            if not spans:
                continue
            text = clean("".join(s["text"] for s in l["spans"]))
            x0, y0, x1, y1 = l["bbox"]
            if y1 < HEADER_Y or y0 > FOOTER_Y:
                if m := re.search(r"\bPages?\s+(\S+)", text):
                    meta["label"] = m.group(1)
                if "CHG 1" in text:
                    meta["chg1"] = True
                continue
            lead = ""
            for s in l["spans"]:
                if not s["text"].strip() and not lead:
                    continue
                if not is_bold(s):
                    break
                lead += s["text"]
            first = spans[0]
            lines.append(Line(pno, x0, y0, x1, y1, text, first["size"], first["font"],
                              is_bold(first), clean(lead)))
    return lines, meta


HEADING_RE = re.compile(r"^\s*(CHAPTER|SECTION|APPENDIX)\s+\d+", re.I)
CAPTION_RE = re.compile(r"^\s*(TABLE|FIGURE)\s+(\d{1,2}[-–]\d{1,3}[a-z]?)\b\.?", re.I)


def classify(lines: list[Line], mid: float) -> None:
    prev_heading = None
    for ln in sorted(lines, key=lambda l: (l.y0, l.x0)):
        ln.col = 0 if ln.x0 < mid - 10 else 1
        arial = "Arial" in ln.font
        if arial and ln.bold and ln.size >= 11.5 and (
                HEADING_RE.match(ln.text)
                or (prev_heading and -4 < ln.y0 - prev_heading.y1 < 12)):
            ln.kind = "heading"
            prev_heading = ln
            continue
        prev_heading = None
        # captions are bold, except a few set in plain 10 pt Times (TABLE 7-6)
        if CAPTION_RE.match(ln.text) and (ln.bold and ln.size <= 12.5
                                          or not arial and ln.size < 11):
            ln.kind = "caption"
        elif not arial and ln.size >= 11:
            ln.kind = "body"
        else:
            ln.kind = "aux"
    # a caption that wraps: the next line just under it, in the same column
    for c in [l for l in lines if l.kind == "caption"]:
        last = c
        for ln in sorted(lines, key=lambda l: l.y0):
            if (ln.kind == "aux" and abs(ln.size - c.size) < 1.5
                    and 0 <= ln.y0 - last.y1 < 5
                    and ln.x0 >= c.x0 - 30 and ln.x1 <= max(c.x1, c.x0 + 260) + 30):
                ln.kind = "caption-cont"
                c.text = f"{c.text.rstrip()} {ln.text.strip()}"
                last = ln


def crossing(x0: float, x1: float, mid: float) -> bool:
    return x0 < mid - 15 and x1 > mid + 15


def reading_order(lines: list[Line], images: list, mid: float) -> list[Line]:
    """Body, heading and caption lines in reading order (two columns, banded)."""
    seps = [(l.y0, l.y1) for l in lines
            if l.kind in ("heading", "caption") and crossing(l.x0, l.x1, mid)]
    # full-width tables/figures: cluster aux lines by vertical gaps
    aux = sorted((l for l in lines if l.kind == "aux"), key=lambda l: l.y0)
    cluster = []
    for ln in aux + [None]:
        if cluster and (ln is None or ln.y0 - max(c.y1 for c in cluster) > 20):
            x0, x1 = min(c.x0 for c in cluster), max(c.x1 for c in cluster)
            if crossing(x0, x1, mid):
                seps.append((min(c.y0 for c in cluster), max(c.y1 for c in cluster)))
            cluster = []
        if ln is not None:
            cluster.append(ln)
    seps += [(b[1], b[3]) for b in images if crossing(b[0], b[2], mid)]
    seps.sort()

    def band(l: Line) -> int:
        return sum(1 for s0, s1 in seps if s1 <= l.y0 + 1)

    flow = [l for l in lines if l.kind in ("body", "heading", "caption", "inline")]
    full = {id(l) for l in flow if l.kind != "body" and crossing(l.x0, l.x1, mid)}
    return sorted(flow, key=lambda l: (band(l), 0 if id(l) in full else 1,
                                       0 if id(l) in full else l.col, l.y0, l.x0))


# ---------------------------------------------------------------- chunks

PARA_RE = re.compile(r"^\s*(\d{1,2})[-–](\d{1,3})\.")
RESERVED_RE = re.compile(r"RESERVED", re.I)
WCN_RE = re.compile(r"^\s*(WARNING|CAUTION|NOTE)S?\b")
SUBLABEL_RE = re.compile(r"^\s*([a-z])\.\s")
ANY_LABEL_RE = re.compile(r"^\s*(?:[a-z]\.|\(\w{1,3}\)|\d{1,2}\.)\s")


@dataclass
class Block:
    """A paragraph or warning being assembled."""
    kind: str                     # para | warning
    para_id: str
    page: int
    label: str | None
    heading_path: list[str]
    title: str = ""
    title_open: bool = False
    lines: list = field(default_factory=list)   # (text, sublabel or None, page, label)
    wkind: str = ""               # WARNING / CAUTION / NOTE
    last: Line | None = None
    chg1: bool = False


class Builder:
    def __init__(self):
        self.chunks: list[dict] = []
        self.loc_ids: set[str] = set()
        self.chapter = self.section = ""
        self.para: Block | None = None
        self.warn: Block | None = None
        self.warn_count: dict[tuple, int] = {}
        self.stats = {"reserved": 0, "inline_lines": 0}

    # -- ids -------------------------------------------------------------
    def unique_loc(self, loc: str, page: int) -> str:
        if loc in self.loc_ids:
            loc = f"{loc}@p{page}"
        self.loc_ids.add(loc)
        return loc

    def emit(self, *, loc: str, page: int, label: str | None, type_: str, text: str,
             heading_path: list[str], cite: str, extra: dict) -> dict:
        loc = self.unique_loc(loc, page)
        if "@p" in loc and type_ in ("text", "warning"):
            cite = cite.replace("¶", "¶(second) ")
        rec = {
            "id": f"faa:p{page:03d}:{loc}",
            "doc": DOC,
            "loc": {"scheme": "faa-para", "id": loc, "cite": cite},
            "heading_path": [h for h in heading_path if h],
            "type": type_,
            "text": text,
            "refs_out": [],
        }
        if label:
            rec["page"] = label
        rec["extra"] = {"pdf_page": page, **extra}
        self.chunks.append(rec)
        return rec

    def path(self) -> list[str]:
        return [self.chapter, self.section]

    # -- flow ------------------------------------------------------------
    def heading(self, text: str) -> None:
        text = re.sub(r"\s+", " ", text).strip()
        self.close_para()
        if re.match(r"CHAPTER\s", text, re.I):
            self.chapter, self.section = text, ""
            self._last_heading = "chapter"
        elif re.match(r"SECTION\s", text, re.I):
            self.section = text
            self._last_heading = "section"
        elif self._last_heading == "chapter":
            self.chapter += " " + text
        else:
            self.section += " " + text

    _last_heading = ""

    def body(self, ln: Line, meta: dict) -> None:
        lead = ln.bold_lead.strip()
        m = PARA_RE.match(ln.text) if ln.bold else None
        if m and ln.size >= 11:
            self.close_para()
            if RESERVED_RE.search(ln.text):
                self.stats["reserved"] += 1
                return
            pid = f"{m.group(1)}-{m.group(2)}"
            title = re.sub(r"^\s*\d{1,2}[-–]\d{1,3}\.\s*", "", lead)
            self.para = Block("para", pid, ln.page, meta["label"],
                              self.path() + [""], title=title, chg1=meta["chg1"])
            # a title that fills the line and has no closing "." wraps onto the next line
            self.para.title_open = lead == ln.text.strip() and (
                not title.strip() or not lead.endswith("."))
            self.set_title(title)
            self.para.lines.append((ln.text, None, ln.page, meta["label"]))
            self.para.last = ln
            return
        if self.para is None:
            return    # text before the first paragraph of a chapter (none expected)
        if self.para.title_open:
            self.para.title_open = bool(lead) and lead == ln.text.strip() and not lead.endswith(".")
            if lead:
                self.set_title(join_lines([self.para.title, lead]))
        w = WCN_RE.match(lead) if ln.bold else None
        if w:
            self.close_warn()
            self.warn = Block("warning", self.para.para_id, ln.page, meta["label"],
                              self.para.heading_path, wkind=w.group(1).upper(),
                              chg1=meta["chg1"])
            self.warn.lines.append((ln.text, None, ln.page, meta["label"]))
            self.warn.last = ln
            return
        if self.warn is not None:
            prev = self.warn.last
            gap = ln.y0 - prev.y1 if (ln.page == prev.page and ln.col == prev.col) else 0
            if (ln.bold and ANY_LABEL_RE.match(ln.text)) or gap > 8:
                self.close_warn()
            else:
                self.warn.lines.append((ln.text, None, ln.page, meta["label"]))
                self.warn.last = ln
                return
        sub = SUBLABEL_RE.match(lead) if ln.bold else None
        self.para.lines.append((ln.text, sub.group(1) if sub else None, ln.page, meta["label"]))
        self.para.last = ln

    def set_title(self, title: str) -> None:
        self.para.title = title
        clean_title = title.replace("\u00ad", "").strip().rstrip(".").strip()
        self.para.heading_path[-1] = f"{self.para.para_id}. {clean_title}"

    def close_warn(self) -> None:
        w = self.warn
        if w is None:
            return
        self.warn = None
        key = (w.para_id, w.wkind)
        n = self.warn_count[key] = self.warn_count.get(key, 0) + 1
        lab = f" {w.label}" if w.label else ""
        self.emit(loc=f"{w.para_id}#{w.wkind.lower()}-{n}", page=w.page, label=w.label,
                  type_="warning", text=join_lines([t for t, *_ in w.lines]),
                  heading_path=w.heading_path,
                  cite=f"{CITE} ¶{w.para_id} {w.wkind}, p.{lab}".rstrip(", p."),
                  extra={"parent": w.para_id, "kind": w.wkind, "chg1_page": w.chg1})

    def close_para(self) -> None:
        self.close_warn()
        p = self.para
        if p is None:
            return
        self.para = None
        for part_loc, lines, subs in split_parts(p):
            page, label = lines[0][2], lines[0][3]
            lab = f", p. {label}" if label else ""
            self.emit(loc=part_loc, page=page, label=label, type_="text",
                      text=join_lines([t for t, *_ in lines]),
                      heading_path=p.heading_path,
                      cite=f"{CITE} ¶{part_loc}{lab}",
                      extra={"para": p.para_id, "subparas": subs, "chg1_page": p.chg1}
                      if subs else {"para": p.para_id, "chg1_page": p.chg1})

    def finish(self) -> None:
        self.close_para()


def split_parts(p: Block):
    """Yield (loc_id, lines, subparas). Long paragraphs split at a./b. labels."""
    words = sum(len(t.split()) for t, *_ in p.lines)
    if words <= SPLIT_WORDS or not any(s for _, s, *_ in p.lines):
        yield p.para_id, p.lines, [s for _, s, *_ in p.lines if s]
        return
    groups, cur = [], []          # each group: lines of one sub-paragraph (or the lead)
    for item in p.lines:
        if item[1] and cur:
            groups.append(cur)
            cur = []
        cur.append(item)
    groups.append(cur)
    parts, cur, cur_words = [], [], 0
    for g in groups:
        gw = sum(len(t.split()) for t, *_ in g)
        if cur and cur_words + gw > SPLIT_WORDS:
            parts.append(cur)
            cur, cur_words = [], 0
        cur += g
        cur_words += gw
    parts.append(cur)
    for i, lines in enumerate(parts):
        subs = [s for _, s, *_ in lines if s]
        loc = p.para_id if i == 0 else f"{p.para_id}{subs[0]}"
        yield loc, lines, subs


# ---------------------------------------------------------------- tables & figures

def rows_from_lines(lines: list[Line]) -> list[str]:
    rows: list[list[Line]] = []
    for ln in sorted(lines, key=lambda l: (l.y0, l.x0)):
        if rows and abs(rows[-1][0].y0 - ln.y0) < 3:
            rows[-1].append(ln)
        else:
            rows.append([ln])
    return [" | ".join(join_lines([l.text]) for l in sorted(r, key=lambda l: l.x0)) for r in rows]


def assign_aux(lines: list[Line], open_table: dict | None) -> tuple[dict, list[Line]]:
    """Give each small-font line to a table or figure caption.

    Returns (lines owned per caption, lines continuing last page's table). Lines
    with no caption are marked kind="inline": they are part of the paragraph they
    sit in (e.g. the untitled tube-support table on PDF p398).
    """
    caps = sorted((l for l in lines if l.kind == "caption"), key=lambda l: l.y0)
    aux = [l for l in lines if l.kind == "aux"]
    owned: dict[int, list[Line]] = {id(c): [] for c in caps}
    cont: list[Line] = []

    def nearest(cands: list[Line], a: Line, above: bool) -> Line | None:
        """Closest caption vertically; one sharing the line's column wins."""
        def cost(c: Line) -> float:
            dist = a.y0 - c.y1 if above else c.y0 - a.y1
            same_col = a.x0 < c.x1 + 40 and a.x1 > c.x0 - 40 or crossing(c.x0, c.x1, 306)
            return dist + (0 if same_col else 400)
        return min(cands, key=cost) if cands else None

    def cap_kind(c: Line | None) -> str:
        return CAPTION_RE.match(c.text).group(1).upper() if c is not None else ""

    for a in aux:
        cab = nearest([c for c in caps if c.y1 <= a.y0 + 1], a, above=True)
        cbe = nearest([c for c in caps if c.y0 >= a.y1 - 1], a, above=False)
        if cap_kind(cab) == "TABLE":
            owned[id(cab)].append(a)
        elif cap_kind(cbe) == "FIGURE":
            owned[id(cbe)].append(a)
        elif cab is None and open_table is not None:
            cont.append(a)
        elif cap_kind(cab) == "FIGURE":       # a figure whose caption sits above it
            owned[id(cab)].append(a)
        else:
            a.kind = "inline"
    return owned, cont


def build_floats(b: Builder, lines: list[Line], meta: dict, open_table: dict | None,
                 ctx: dict, owned: dict, cont: list[Line]) -> dict | None:
    """Emit table and figure chunks for one page. Returns the table still open at page end."""
    caps = sorted((l for l in lines if l.kind == "caption"), key=lambda l: l.y0)
    if cont and open_table is not None:     # table continued from the previous page
        emit_table(b, open_table["num"], open_table["title"], cont, meta, ctx)

    still_open = None
    for c in caps:
        m = CAPTION_RE.match(c.text)
        kind, num = m.group(1).upper(), m.group(2).replace("–", "-")
        title = join_lines([c.text])
        if kind == "TABLE":
            emit_table(b, num, title, owned[id(c)], meta, ctx, caption=c)
            still_open = {"num": num, "title": title}
        else:
            text = join_lines([title] + rows_from_lines(owned[id(c)]))
            lab = f", p. {meta['label']}" if meta["label"] else ""
            b.emit(loc=f"figure-{num}", page=c.page, label=meta["label"], type_="figure",
                   text=text, heading_path=b.path(), cite=f"{CITE} Figure {num}{lab}",
                   extra={"parent": ctx.get("para"), "chg1_page": meta["chg1"]})
    if caps:
        return still_open
    return open_table if cont else None


def emit_table(b: Builder, num: str, title: str, cells: list[Line], meta: dict, ctx: dict,
               caption: Line | None = None) -> None:
    rows = rows_from_lines(cells)
    base = f"table-{num}"
    seg = ctx.setdefault("table_segments", {})
    seg[base] = seg.get(base, 0) + 1
    loc = base if seg[base] == 1 else f"{base}#{seg[base]}"
    lab = f", p. {meta['label']}" if meta["label"] else ""
    page = caption.page if caption else (cells[0].page if cells else 0)
    head = title if caption else f"{title} (continued)"
    b.emit(loc=loc, page=page, label=meta["label"], type_="table",
           text="\n".join([head] + rows), heading_path=b.path(),
           cite=f"{CITE} Table {num}{lab}",
           extra={"parent": ctx.get("para"), "segment": seg[base],
                  "chg1_page": meta["chg1"]})


# ---------------------------------------------------------------- refs & specs

ID = r"\d{1,2}[-–]\d{1,3}[a-z]?(?:\s?[a-z]\b)?(?:\(\w{1,4}\))*"
REF_RE = re.compile(
    rf"\b(?P<kind>(?:sub-?)?paragraphs?|figures?|tables?|appendix)\s+"
    rf"(?P<ids>(?:{ID}|\d)(?:\s*(?:,|and|or|through|thru|–|-)\s*(?:{ID}|\d))*)", re.I)
ID_RE = re.compile(ID)
SPEC_RE = re.compile(
    r"\b(?:MIL[-–](?:STD|HDBK|PRF|DTL|[A-Z])[-–]?\d+[A-Z]?(?:/\d+[A-Z]?)?"  # MIL-W5088 typo too
    r"|FED[-–]STD[-–]\d+[A-Z]?|TSO[-–]C\d+[a-z]?|ANC[-–]\d+"
    r"|(?:AN|MS|NASM|NAS)[-–\s]?\d{1,5}[A-Z]{0,2}(?:[-–]\d+[A-Z]*)?"
    r"|AMS\s?\d{4}[A-Z]?|AMS[-–][A-Z]+[-–]\d+"
    r"|(?:SAE,?\s)?(?:ARP|AIR)[-–\s]?\d{3,4}[A-Z]?|SAE\s(?:AS|J)\s?\d+[A-Z]?|AS\s?\d{4,5}[A-Z]?"
    r"|ASTM[-–\s][A-Z][-–\s]?\d+"
    r"|A[-–]A[-–]\d+|(?:TT|QQ|VV|PPP|MMM|O|L|P)[-–][A-Z][-–]\d+[A-Z]?(?:/\d+)?)")
RELATIVE_RE = re.compile(
    r"\b(?:see\s+)?page\s+\d{1,2}[-–]\d{1,3}\b[^.;)]{0,20}"
    r"|\b(?:preceding|previous|following)\s+(?:paragraph|figure|table)s?\b"
    r"|\b(?:paragraph|figure|table)s?\s+(?:above|below)\b"
    r"|\bthis\s+(?:table|figure)\b"
    r"|as outlined in\s+[\"“][^\"”]{3,60}[\"”]", re.I)


def norm_spec(s: str) -> str:
    """One written form per spec, so the same spec always matches itself."""
    s = re.sub(r"\s+", " ", s.replace("–", "-"))
    s = re.sub(r"^(?:SAE,? )?(ARP|AIR)[- ]?", r"SAE \1", s)          # SAE ARP-1870 -> SAE ARP1870
    s = re.sub(r"^ASTM[- ]([A-Z])[- ]?", r"ASTM \1", s)               # ASTM-E-1417 -> ASTM E1417
    s = re.sub(r"^(AN|MS|NASM|NAS)[- ](?=\d)", r"\1", s)             # MS-21919 -> MS21919
    s = re.sub(r"^(MIL-(?:STD|HDBK|PRF|DTL|[A-Z]))(?=\d)", r"\1-", s)  # MIL-W5088 -> MIL-W-5088
    return re.sub(r"(?<=[A-Z]) (?=\d)", "", s)


def expand_ids(kind: str, ids: str) -> list[str]:
    """'2-9 through 2-12' -> each id; '7-5 through 7-5b' -> 7-5, 7-5a, 7-5b."""
    ids = ids.replace("–", "-")
    if kind.startswith("appendix"):
        return re.findall(r"\b\d\b", ids)
    toks = re.split(r"\s*(,|\band\b|\bor\b|\bthrough\b|\bthru\b)\s*", ids)
    out: list[str] = []
    pending_range = False
    for t in toks:
        t = t.strip()
        if t in ("through", "thru"):
            pending_range = True
            continue
        if not t or t in (",", "and", "or"):
            continue
        m = ID_RE.match(t)
        if not m:
            continue
        cur = re.sub(r"\s", "", m.group(0))
        if pending_range and out:
            out += range_between(out[-1], cur)[1:]
        else:
            out.append(cur)
        pending_range = False
    return out


def range_between(a: str, b: str) -> list[str]:
    pa = re.match(r"(\d+)-(\d+)([a-z]?)", a)
    pb = re.match(r"(\d+)-(\d+)([a-z]?)", b)
    if not pa or not pb or pa.group(1) != pb.group(1):
        return [a, b]
    ch, na, la = pa.group(1), int(pa.group(2)), pa.group(3)
    nb, lb = int(pb.group(2)), pb.group(3)
    if na == nb and lb:
        start = ord(la) if la else ord("a") - 1
        return [a] + [f"{ch}-{na}{chr(c)}" for c in range(start + 1, ord(lb) + 1)]
    if not la and not lb and na < nb <= na + 40:
        return [f"{ch}-{n}" for n in range(na, nb + 1)]
    return [a, b]


class Resolver:
    def __init__(self, chunks: list[dict]):
        self.locs = {c["loc"]["id"] for c in chunks}
        self.sub = {}             # "4-58g" -> part loc holding sub-paragraph g
        for c in chunks:
            para = c.get("extra", {}).get("para")
            for s in c.get("extra", {}).get("subparas", []):
                self.sub.setdefault(f"{para}{s}", c["loc"]["id"])

    def para(self, raw: str) -> str:
        m = re.match(r"(\d+-\d+)([a-z]?)", raw)
        base, letter = m.group(1), m.group(2)
        if letter and f"{base}{letter}" in self.sub:
            return self.sub[f"{base}{letter}"]
        return base

    def target(self, kind: str, raw: str) -> str:
        k = kind.lower()
        if k.startswith(("paragraph", "sub")):
            return self.para(raw)
        if k.startswith("figure"):
            return f"figure-{re.match(r'\d+-\d+[a-z]?', raw).group(0)}"
        if k.startswith("table"):
            return f"table-{re.match(r'\d+-\d+', raw).group(0)}"
        return f"appendix-{raw}"


def add_refs_and_specs(chunks: list[dict], overrides: list[dict]) -> list[dict]:
    """Fill refs_out/specs. Returns relative-ref phrases not covered by an override.

    An override is either a phrase fix (chunk/phrase/refs) or an alias
    (alias/target): every ref to the alias goes to the target instead.
    """
    res = Resolver(chunks)
    by_loc = {c["loc"]["id"]: c for c in chunks}
    aliases = {o["alias"]: o["target"] for o in overrides if "alias" in o}
    for alias, target in aliases.items():
        if alias in by_loc:
            sys.exit(f"ref_overrides.yaml: alias {alias!r} is a real loc.id; it would hide it")
        if target not in by_loc:
            sys.exit(f"ref_overrides.yaml: alias target {target!r} has no chunk")
    overrides = [o for o in overrides if "alias" not in o]
    covered = set()
    for o in overrides:
        c = by_loc.get(o["chunk"])
        if c is None:
            sys.exit(f"ref_overrides.yaml: no chunk with loc.id {o['chunk']!r}")
        if o["phrase"] not in c["text"]:
            sys.exit(f"ref_overrides.yaml: phrase {o['phrase']!r} not in chunk {o['chunk']}")
        covered.add((o["chunk"], o["phrase"]))
    unhandled = []
    for c in chunks:
        own = c["loc"]["id"]
        refs, raw = [], []
        for m in REF_RE.finditer(c["text"]):
            for rid in expand_ids(m.group("kind"), m.group("ids")):
                t = res.target(m.group("kind"), rid)
                t = aliases.get(t, t)
                raw.append(f"{m.group('kind').lower()} {rid}")
                if t != own and not own.startswith(t + "#") and t not in refs:
                    refs.append(t)
        for o in overrides:
            if o["chunk"] == own:
                refs += [r for r in (aliases.get(r, r) for r in o["refs"]) if r not in refs]
        c["refs_out"] = refs
        if raw:
            c["extra"]["refs_raw"] = raw
        specs = []
        for m in SPEC_RE.finditer(c["text"]):
            s = norm_spec(m.group(0))
            if s not in specs:
                specs.append(s)
        if specs:
            c["specs"] = specs
        for m in RELATIVE_RE.finditer(c["text"]):
            phrase = m.group(0).strip()
            if not any(k == own and p in c["text"][max(0, m.start() - 80):m.end() + 80]
                       for k, p in covered):
                if {"chunk": own, "phrase": phrase} not in unhandled:
                    unhandled.append({"chunk": own, "phrase": phrase})
    return unhandled


# ---------------------------------------------------------------- main

def parse(pdf_path: Path = PDF, overrides_path: Path = OVERRIDES) -> tuple[list[dict], dict]:
    doc = pymupdf.open(pdf_path)
    b = Builder()
    open_table = None
    ctx: dict = {}
    appendix = None               # (number, [lines]) once the appendices start
    for pno in range(BODY_START, doc.page_count + 1):
        if pno in DUP_PAGES:
            continue
        page = doc[pno - 1]
        lines, meta = page_lines(page, pno)
        if not lines:
            continue
        mid = page.rect.width / 2
        classify(lines, mid)
        heads = [l for l in lines if l.kind == "heading"]
        if appendix is not None or any(re.match(r"\s*APPENDIX\s+\d", l.text, re.I) for l in heads):
            b.finish()
            m = next((re.match(r"\s*APPENDIX\s+(\d)", l.text, re.I) for l in lines
                      if re.match(r"\s*APPENDIX\s+\d", l.text, re.I)), None)
            if m:
                appendix = m.group(1)
            ordered = sorted(lines, key=lambda l: (l.col, l.y0, l.x0))
            loc = f"appendix-{appendix}"
            seg = ctx.setdefault("appendix_segments", {})
            seg[loc] = seg.get(loc, 0) + 1
            b.emit(loc=loc if seg[loc] == 1 else f"{loc}#{seg[loc]}", page=pno,
                   label=meta["label"], type_="text",
                   text=join_lines([l.text for l in ordered]),
                   heading_path=[f"APPENDIX {appendix}"],
                   cite=f"{CITE} Appendix {appendix}, PDF p. {pno}",
                   extra={"chg1_page": meta["chg1"]})
            continue
        owned, cont = assign_aux(lines, open_table)
        b.stats["inline_lines"] += sum(1 for l in lines if l.kind == "inline")
        imgs = [tuple(i["bbox"]) for i in page.get_image_info()]
        heading_buf: list[Line] = []
        for ln in reading_order(lines, imgs, mid):
            if ln.kind == "heading":
                heading_buf.append(ln)
                continue
            if heading_buf:
                apply_headings(b, heading_buf)
                heading_buf = []
            if ln.kind == "body":
                b.body(ln, meta)
            elif ln.kind == "inline":
                b.body(ln, meta)
            elif ln.kind == "caption":
                b.close_warn()
        if heading_buf:
            apply_headings(b, heading_buf)
        ctx["para"] = b.para.para_id if b.para else None
        open_table = build_floats(b, lines, meta, open_table, ctx, owned, cont)
    b.finish()
    overrides = yaml.safe_load(overrides_path.read_text()) or [] if overrides_path.exists() else []
    b.stats["unhandled_relative_refs"] = add_refs_and_specs(b.chunks, overrides)
    return b.chunks, b.stats


def apply_headings(b: Builder, lines: list[Line]) -> None:
    """Heading lines arrive in reading order; a wrapped heading continues the last one."""
    groups: list[list[Line]] = []
    for ln in lines:
        if groups and not HEADING_RE.match(ln.text) and 0 <= ln.y0 - groups[-1][-1].y1 < 12:
            groups[-1].append(ln)
        else:
            groups.append([ln])
    for g in groups:
        b.heading(join_lines([l.text for l in g]))


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("pdf", nargs="?", type=Path, default=PDF)
    ap.add_argument("-o", "--out", type=Path, default=OUT)
    args = ap.parse_args()
    if not args.pdf.exists():
        sys.exit(f"{args.pdf} not found; run: python corpus/faa/fetch.py")
    chunks, stats = parse(args.pdf)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    with args.out.open("w") as f:
        for c in chunks:
            f.write(json.dumps(c, ensure_ascii=False) + "\n")
    stats_path = args.out.with_name("faa_parse_stats.json")
    stats_path.write_text(json.dumps(stats, indent=2, ensure_ascii=False))
    print(f"{len(chunks)} chunks -> {args.out}")
    print(f"stats -> {stats_path}")


if __name__ == "__main__":
    main()
