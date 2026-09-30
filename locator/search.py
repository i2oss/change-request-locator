"""Change request text -> candidate spots, with no LLM (SPEC §5.2 steps 1-3, baseline).

    request = read_request(text)
    with Index(path) as index:
        candidates = locate(index, request, embed=query_embedder(index.meta["embed_model"]))

1. Read the request: regexes pull spec numbers, paragraph/table/figure refs,
   values ("0.10 inch") and the remaining meaningful words.
2. Search: exact lookups on specs and loc.id; FTS5 (BM25) and vector search,
   merged by reciprocal rank fusion. Exact hits are always kept.
3. Expand ripples by plain lookup: chunks pointing at a hit, chunks sharing a
   spec with a hit, warnings repeating a hit warning, applicability splits.

Each candidate records how it was found (`Reason`s). locator/tiers.py turns that
into tiers and "why" lines. Ranks stay inside the pipeline: no score is ever shown.
"""
from __future__ import annotations

import json
import re
import sqlite3
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

import numpy as np

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from locator import config  # noqa: E402
from parsers.faa_pdf import ID, REF_RE, SPEC_RE, Resolver, expand_ids, norm_spec  # noqa: E402

QueryEmbed = Callable[[list[str]], np.ndarray]

# Search knobs. Tune them on the tune split only (SPEC §6.4).
RRF_K = 60            # standard reciprocal rank fusion constant
LIST_K = 50           # how deep each ranked list (words, values, meaning) goes
FUSED_KEEP = 12       # non-exact direct spots kept after fusion
RIPPLE_SEEDS = 5      # top fused spots whose ripples are expanded (exact hits always are)
SPEC_FANOUT = 25      # a spec shared by more chunks than this says nothing; skip it
WARNING_OVERLAP = 0.6  # share of the shorter warning's word pairs found in the other
RIPPLE_ORDER = ("points_to", "same_warning", "same_spec", "variant")   # strongest first


# ---------------------------------------------------------------- 1. read the request

PARA_MARK_RE = re.compile(rf"(?:¶|\bpara\.?)\s*(?P<id>{ID})", re.I)
NUMBER = r"\d+[-\s]\d+/\d+|\d+/\d+|\d*\.\d+|\d+"
UNIT = (r"in\.?[-\s]?lbs?\.?|ft\.?[-\s]?lbs?\.?|inch(?:es)?|in\.|psi|pounds?|lbs?\.?|percent|%"
        r"|°\s?[FC]\b|degrees?(?:\s[FC]\b)?|volts?|amps?|mm|cm|feet|ft\.|hours?|minutes?")
VALUE_RE = re.compile(rf"(?<![\w./-])(?:{NUMBER})\s?(?:{UNIT})(?![\w-])", re.I)
TOKEN_RE = re.compile(r"[A-Za-z0-9](?:[A-Za-z0-9'-]*[A-Za-z0-9])?")
STOPWORDS = set("""
    the and for with from into onto that this these those there their them they then than
    are was were been being has have had not but all any each every per its our your you
    please update updated updates change changes changed changing revise revised revision
    replace replaced replaces replacement correct corrected fix fixed amend amended edit
    edits new old now should must shall will would could can may also need needs needed
    request requested paragraph paragraphs para table tables figure figures spec specs
    specification specifications standard throughout manual document where which what when
    who how see refer reference references section chapter page pages instead use used
    place places appear appears appearing wherever whenever every occurrence occurrences
    instance instances mention mentions mentioned listed supersede superseded supersedes
    cancel cancelled canceled obsolete retire retired
""".split())


@dataclass(frozen=True)
class Request:
    text: str
    specs: tuple[str, ...] = ()
    refs: tuple[str, ...] = ()     # loc.ids, e.g. "7-144", "table-7-4"
    values: tuple[str, ...] = ()
    terms: tuple[str, ...] = ()    # words, plus codes SPEC_RE doesn't know ("pn-4471-2")


def read_request(text: str) -> Request:
    """Pull specs, refs, values and meaningful words out of a change request."""
    rest = text
    specs = _unique(norm_spec(m.group(0)) for m in SPEC_RE.finditer(text))
    rest = SPEC_RE.sub(" ", rest)

    resolver = Resolver([])
    refs = []
    for m in REF_RE.finditer(rest):
        refs += [resolver.target(m.group("kind"), rid)
                 for rid in expand_ids(m.group("kind"), m.group("ids"))]
    for m in PARA_MARK_RE.finditer(rest):
        refs.append(resolver.para(re.sub(r"\s", "", m.group("id").replace("–", "-"))))
    rest = PARA_MARK_RE.sub(" ", REF_RE.sub(" ", rest))

    values = _unique(re.sub(r"\s+", " ", m.group(0)) for m in VALUE_RE.finditer(rest))
    rest = VALUE_RE.sub(" ", rest)

    terms = _unique(t.lower() for t in TOKEN_RE.findall(rest)
                    if len(t) >= 3 and re.search("[A-Za-z]", t) and t.lower() not in STOPWORDS)
    return Request(text, tuple(specs), tuple(_unique(refs)), tuple(values), tuple(terms))


def _unique(items) -> list:
    return list(dict.fromkeys(items))


def term_pattern(term: str) -> str:
    """Regex for a request term in chunk text: words allow endings (bleed ~ bleeding),
    codes must match whole."""
    if term.isalpha():
        return rf"\b{re.escape(term[:max(4, len(term) - 2)])}\w*"
    return rf"(?<![\w-]){re.escape(term)}(?![\w-])"


def value_pattern(value: str) -> str:
    """Regex for a request value in chunk text, as loose as the keyword index:
    "0.10 inches" also finds "0.10-inch"."""
    parts = [re.escape(t) if t.isdigit() else re.escape(t[:max(4, len(t) - 2)]) + "[a-z]*"
             for t in re.findall(r"[A-Za-z]+|\d+", value)]
    return r"(?<![\w.])" + r"[\W_]*".join(parts) + r"(?!\d)"


# ---------------------------------------------------------------- the index

@dataclass(frozen=True)
class Chunk:
    seq: int
    id: str
    loc_id: str
    cite: str
    type: str
    text: str
    heading_path: tuple[str, ...]
    specs: tuple[str, ...]
    applies_to: tuple[str, ...]


def spec_key(spec: str) -> str:
    """MIL-W-5088L and MIL-W-5088 (or TSO-C91a and TSO-C91) are one spec at
    different revisions."""
    return re.sub(r"(?<=\d)[A-Za-z]$", "", spec)


def stated_spec(chunk: Chunk, spec: str) -> str:
    """The spec as the chunk writes it, e.g. MIL-W-5088K for a request's MIL-W-5088L."""
    return next((s for s in chunk.specs if spec_key(s) == spec_key(spec)), spec)


class Index:
    """One corpus's SQLite file (scripts/build_index.py), opened read-only."""

    def __init__(self, path: Path | str):
        self.conn = sqlite3.connect(Path(path).resolve().as_uri() + "?mode=ro", uri=True)
        self.meta = dict(self.conn.execute("SELECT key, value FROM meta"))
        self.chunks: dict[str, Chunk] = {}
        for row in self.conn.execute(
                "SELECT seq, id, loc_id, loc_cite, type, text, heading_path, specs, applies_to"
                " FROM chunks ORDER BY seq"):
            seq, id_, loc, cite, type_, text, heading, specs, applies = row
            self.chunks[id_] = Chunk(seq, id_, loc, cite, type_, text, tuple(json.loads(heading)),
                                     tuple(json.loads(specs)), tuple(json.loads(applies)))
        self.by_seq = {c.seq: c for c in self.chunks.values()}
        self.by_spec: dict[str, list[str]] = {}
        for chunk_id, spec in self.conn.execute(
                "SELECT s.chunk_id, s.spec FROM chunk_specs s JOIN chunks c ON c.id = s.chunk_id"
                " ORDER BY c.seq"):
            ids = self.by_spec.setdefault(spec_key(spec), [])
            if chunk_id not in ids:
                ids.append(chunk_id)
        self._vectors: tuple[list[str], np.ndarray] | None = None

    def __enter__(self) -> Index:
        return self

    def __exit__(self, *exc) -> None:
        self.conn.close()

    def scope(self) -> str:
        """What was searched, e.g. "AC 43.13-1B w/ Change 1 (FAA, 9/27/01), 1,320 chunks"."""
        return f"{self.meta['doc']}, {int(self.meta['chunk_count']):,} chunks"

    def vectors(self) -> tuple[list[str], np.ndarray]:
        if self._vectors is None:
            dim = int(self.meta["embed_dim"])
            rows = self.conn.execute(
                "SELECT e.chunk_id, e.vector FROM embeddings e JOIN chunks c ON c.id = e.chunk_id"
                " ORDER BY c.seq").fetchall()
            matrix = np.frombuffer(b"".join(v for _, v in rows), dtype="<f4").reshape(-1, dim)
            self._vectors = [i for i, _ in rows], matrix
        return self._vectors


def query_embedder(model: str) -> QueryEmbed:
    """The index's own model on CPU. Imported here so tests with a fake embedder skip torch."""
    from sentence_transformers import SentenceTransformer

    st = SentenceTransformer(model, device="cpu")
    return lambda texts: st.encode(texts, convert_to_numpy=True)


# ---------------------------------------------------------------- 2. search

@dataclass(frozen=True)
class Reason:
    """One fact about how a candidate was found. tiers.py words it."""
    kind: str        # direct: spec, loc, value, words, meaning
                     # ripple: points_to, same_spec, same_warning, variant
    detail: str = ""  # the spec, value, matched words or variant
    via: str = ""     # loc.id of the hit a ripple hangs off
    seed: str = field(default="", compare=False)   # ... and that hit's chunk id


@dataclass
class Candidate:
    chunk: Chunk
    origin: str                  # "direct" or "ripple"
    rank: int                    # order found, exact hits first; never shown
    reasons: list[Reason]
    exact: bool = False
    seed: str | None = None      # chunk id of the hit its strongest ripple link hangs off


def locate(index: Index, request: Request, embed: QueryEmbed) -> list[Candidate]:
    """Direct spots then ripples, in rank order.

    Exact hits are always direct. A fused spot that is also a ripple is listed as
    that ripple, since a structural link says more than a search rank; its search
    reasons stay on. The top fused spots (the other ripple seeds) give way only to
    ripples of exact hits, not to each other's. A ripple keeps every link that
    reaches it, strongest first.
    """
    exact = _exact_hits(index, request)
    fused = dict([(cid, reasons) for cid, reasons in _fused_hits(index, request, embed)
                  if cid not in exact][:FUSED_KEEP])
    fused_seeds = list(fused)[:RIPPLE_SEEDS]

    ripples: dict[str, list[Reason]] = {}
    for seed in list(exact) + fused_seeds:
        for cid, reason in _ripples(index, index.chunks[seed]):
            if cid in exact or (cid in fused_seeds and seed not in exact):
                continue
            links = ripples.setdefault(cid, [])
            if reason not in links:
                links.append(reason)
    for links in ripples.values():
        links.sort(key=lambda r: RIPPLE_ORDER.index(r.kind))

    found = [Candidate(index.chunks[cid], "direct", 0, reasons, exact=True)
             for cid, reasons in exact.items()]
    found += [Candidate(index.chunks[cid], "direct", 0, reasons)
              for cid, reasons in fused.items() if cid not in ripples]
    found += [Candidate(index.chunks[cid], "ripple", 0, links + fused.get(cid, []),
                        seed=links[0].seed)
              for cid, links in ripples.items()]
    for rank, c in enumerate(found):
        c.rank = rank
    return found


def _exact_hits(index: Index, request: Request) -> dict[str, list[Reason]]:
    hits: dict[str, list[Reason]] = {}
    for ref in request.refs:
        rows = index.conn.execute(
            "SELECT id FROM chunks WHERE loc_id = ? OR loc_id GLOB ? OR loc_id GLOB ?"
            " ORDER BY seq", (ref, f"{ref}[a-z]", f"{ref}#*"))
        for (cid,) in rows:
            hits.setdefault(cid, []).append(Reason("loc", ref))
    for spec in request.specs:
        for cid in index.by_spec.get(spec_key(spec), []):
            hits.setdefault(cid, []).append(Reason("spec", stated_spec(index.chunks[cid], spec)))
    return hits


def _fused_hits(index: Index, request: Request, embed: QueryEmbed):
    """Reciprocal rank fusion of the words, values and meaning lists."""
    lists = {
        "words": _fts(index, [f'"{t}"' for t in request.terms]),
        "value": _fts(index, ['"' + v.replace('"', "") + '"' for v in request.values]),
        "meaning": _nearest(index, request.text, embed),
    }
    fused: dict[str, float] = {}
    for ranked in lists.values():
        for i, cid in enumerate(ranked):
            fused[cid] = fused.get(cid, 0.0) + 1 / (RRF_K + i + 1)
    order = sorted(fused, key=lambda cid: (-fused[cid], index.chunks[cid].seq))
    for cid in order:
        chunk = index.chunks[cid]
        reasons = [Reason("value", v) for v in request.values
                   if cid in lists["value"] and _has_value(chunk.text, v)]
        if cid in lists["words"]:
            reasons.append(Reason("words", ", ".join(_matched_terms(chunk, request.terms))))
        if cid in lists["meaning"]:
            reasons.append(Reason("meaning"))
        yield cid, reasons or [Reason("words")]   # found by keyword, but not in a form we spot


def _fts(index: Index, phrases: list[str]) -> list[str]:
    if not phrases:
        return []
    rows = index.conn.execute(
        "SELECT rowid FROM chunks_fts WHERE chunks_fts MATCH ? ORDER BY bm25(chunks_fts) LIMIT ?",
        (" OR ".join(phrases), LIST_K))
    return [index.by_seq[seq].id for (seq,) in rows]


def _nearest(index: Index, text: str, embed: QueryEmbed) -> list[str]:
    q = np.asarray(embed([config.QUERY_PREFIX + text])[0], dtype=np.float32)
    norm = np.linalg.norm(q)
    if not text.strip() or norm == 0:
        return []
    ids, matrix = index.vectors()
    sims = matrix @ (q / norm)
    top = np.argsort(-sims, kind="stable")[:LIST_K]
    return [ids[i] for i in top if sims[i] > 0]


def _has_value(text: str, value: str) -> bool:
    return re.search(value_pattern(value), text, re.I) is not None


def _matched_terms(chunk: Chunk, terms: tuple[str, ...]) -> list[str]:
    """The request terms the chunk text or its headings state."""
    text = chunk.text + " " + " ".join(chunk.heading_path)
    return [t for t in terms if re.search(term_pattern(t), text, re.I)]


# ---------------------------------------------------------------- 3. ripples

def _ripples(index: Index, hit: Chunk):
    """(chunk id, reason) for every ripple of one hit, by plain lookup."""
    # Chunks whose refs_out point at the hit, or at the paragraph it sits in.
    targets = {hit.loc_id, hit.loc_id.split("#")[0]}
    if m := re.fullmatch(r"(\d+-\d+)[a-z]", hit.loc_id):
        targets.add(m.group(1))
    marks = ",".join("?" * len(targets))
    rows = index.conn.execute(
        f"SELECT DISTINCT r.chunk_id FROM chunk_refs r JOIN chunks c ON c.id = r.chunk_id"
        f" WHERE r.ref IN ({marks}) ORDER BY c.seq", sorted(targets))
    for (cid,) in rows:
        if cid != hit.id:
            yield cid, Reason("points_to", via=hit.loc_id, seed=hit.id)

    for spec in hit.specs:
        sharing = index.by_spec.get(spec_key(spec), [])
        if len(sharing) > SPEC_FANOUT:
            continue
        for cid in sharing:
            if cid != hit.id:
                yield cid, Reason("same_spec", stated_spec(index.chunks[cid], spec),
                                  via=hit.loc_id, seed=hit.id)

    if hit.type == "warning":
        for other in index.chunks.values():
            if other.type == "warning" and other.id != hit.id and _repeats(hit.text, other.text):
                yield other.id, Reason("same_warning", via=hit.loc_id, seed=hit.id)

    for other in index.chunks.values():
        if (other.id != hit.id and other.heading_path == hit.heading_path
                and (other.applies_to or hit.applies_to)
                and set(other.applies_to) != set(hit.applies_to)):
            yield other.id, Reason("variant", ", ".join(other.applies_to) or "all variants",
                                   via=hit.loc_id, seed=hit.id)


LEAD_IN_RE = re.compile(r"^\W*(?:warning|caution|note)s?\b\W*", re.I)


def _repeats(a: str, b: str) -> bool:
    """Most word pairs of the shorter warning also appear in the other one."""
    def pairs(t: str) -> set[tuple[str, str]]:
        words = re.findall(r"[a-z0-9]+", LEAD_IN_RE.sub("", t).lower())
        return set(zip(words, words[1:]))
    pa, pb = pairs(a), pairs(b)
    short, long_ = sorted((pa, pb), key=len)
    return bool(short) and len(short & long_) / len(short) >= WARNING_OVERLAP
