"""Candidates -> tiered spots with a "why" line each, with no LLM (SPEC §5.2 step 4, baseline).

Two tiers, no scores (SPEC §2):
  Likely       every exact hit (a spec or paragraph the request names); the top
               search spots when the request named nothing exact; and strong
               ripples (points to it, repeats its warning) of a Likely spot.
  Check these  everything else locate() kept. It leans toward recall, because
               missing a ripple costs more than skimming an extra spot.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

from locator.search import Candidate, Chunk, Reason, spec_key, term_pattern, value_pattern

# Tier knobs. Tune them on the tune split only (SPEC §6.4).
LIKELY_TOP = 3                # search-only spots in Likely when there are no exact hits
LIKELY_TOP_BESIDE_EXACT = 0   # ... and when there are
STRONG_RIPPLES = {"points_to", "same_warning"}
SNIPPET_CHARS = 220
WHY_PARTS = 2                 # reasons joined into one why line


@dataclass(frozen=True)
class Spot:
    chunk_id: str
    loc_id: str
    cite: str
    type: str
    origin: str               # "direct" or "ripple"
    why: str
    snippet: str
    highlight: str | None     # the part of the snippet to highlight, if any
    highlight_at: int | None  # where it starts in the snippet


@dataclass(frozen=True)
class Tiers:
    likely: list[Spot]
    check: list[Spot]


def tier(candidates: list[Candidate]) -> Tiers:
    cutoff = LIKELY_TOP_BESIDE_EXACT if any(c.exact for c in candidates) else LIKELY_TOP
    likely_ids: set[str] = set()
    searched = 0
    for c in candidates:
        if c.origin != "direct":
            continue
        if c.exact:
            likely_ids.add(c.chunk.id)
        else:
            if searched < cutoff:
                likely_ids.add(c.chunk.id)
            searched += 1
    for c in candidates:   # after every direct spot is placed, since ripples follow them
        if c.origin == "ripple" and any(r.kind in STRONG_RIPPLES and r.seed in likely_ids
                                        for r in c.reasons):
            likely_ids.add(c.chunk.id)

    order = sorted(candidates, key=lambda c: (c.origin != "direct", c.rank))
    spots = {c.chunk.id: _spot(c) for c in order}
    return Tiers([s for i, s in spots.items() if i in likely_ids],
                 [s for i, s in spots.items() if i not in likely_ids])


# ---------------------------------------------------------------- why lines

TEMPLATES = {
    "spec": "names spec {detail}",
    "loc": "is {detail_loc}, named in the request",
    "value": "states {detail}",
    "words": "mentions {detail}",
    "meaning": "close in meaning to the request",
    "points_to": "points to {via}",
    "same_spec": "same spec {detail} as {via}",
    "same_warning": "repeats the warning at {via}",
    "variant": "{detail} version of {via}",
}


def why(reasons: list[Reason]) -> str:
    parts: list[str] = []
    for r in reasons:
        if r.kind == "words" and not r.detail:
            text = "matches words in the request"
        else:
            text = TEMPLATES[r.kind].format(detail=r.detail, detail_loc=show_loc(r.detail),
                                            via=show_loc(r.via))
        if text not in parts:
            parts.append(text)
    return "; ".join(parts[:WHY_PARTS])


def show_loc(loc_id: str) -> str:
    """7-144 -> ¶7-144, table-7-4 -> table 7-4, 1-4#caution-1 -> ¶1-4 caution 1."""
    base, _, inner = loc_id.partition("#")
    if m := re.fullmatch(r"(table|figure|appendix)-(.+)", base):
        base = f"{m.group(1)} {m.group(2)}"
    elif re.fullmatch(r"\d+-\d+[a-z]?", base):
        base = f"¶{base}"
    return f"{base} {inner.replace('-', ' ')}" if inner else base


# ---------------------------------------------------------------- snippets

def _spot(c: Candidate) -> Spot:
    snippet, highlight_at, highlight = _snippet(c.chunk.text, _highlight(c.chunk, c.reasons))
    return Spot(c.chunk.id, c.chunk.loc_id, c.chunk.cite, c.chunk.type, c.origin,
                why(c.reasons), snippet, highlight, highlight_at)


def _highlight(chunk: Chunk, reasons: list[Reason]) -> re.Match | None:
    """Where in the chunk text the first findable reason shows."""
    for r in reasons:
        patterns = []
        if r.kind in ("spec", "same_spec"):
            patterns = [re.escape(r.detail), re.escape(spec_key(r.detail)) + r"[A-Z]?\b"]
        elif r.kind == "value":
            patterns = [value_pattern(r.detail)]
        elif r.kind == "words":
            patterns = [term_pattern(t) for t in r.detail.split(", ") if t]
        elif r.kind == "points_to":
            num = re.sub(r"^(?:table|figure|appendix)-", "", r.via.split("#")[0])
            patterns = [rf"(?<![\w-]){re.escape(num)}(?![\w-])"]
        for p in patterns:
            if m := re.search(p, chunk.text, re.I):
                return m
    return None


def _snippet(text: str, m: re.Match | None) -> tuple[str, int | None, str | None]:
    """A window of about SNIPPET_CHARS around the highlight, cut at spaces.
    Returns the snippet, where the highlight starts in it, and the highlight."""
    n = SNIPPET_CHARS
    if len(text) <= n:
        return (text, m.start(), m.group(0)) if m else (text, None, None)
    hs, he = (m.start(), m.end()) if m else (0, 0)
    start = max(0, min(hs - (n - (he - hs)) // 2, len(text) - n))
    end = start + n
    if start > 0 and (sp := text.find(" ", start, hs)) != -1:
        start = sp + 1
    if end < len(text) and (sp := text.rfind(" ", he, end)) != -1:
        end = sp
    body = text[start:end]
    lead = len(body) - len(body.lstrip())
    prefix = "…" if start > 0 else ""
    out = prefix + body.strip() + ("…" if end < len(text) else "")
    if m and start + lead <= hs and he <= end:
        return out, len(prefix) + hs - start - lead, m.group(0)
    return out, None, None


# ---------------------------------------------------------------- output

def render(scope: str, request_line: str, tiers: Tiers) -> str:
    lines = [f"Searched: {scope}", f"Read: {request_line}", ""]
    if not tiers.likely and not tiers.check:
        return "\n".join(lines + [f"No spots found in {scope}."]) + "\n"
    for title, spots in (("Likely", tiers.likely), ("Check these", tiers.check)):
        lines.append(title)
        if not spots:
            lines.append("  (none)")
        for s in spots:
            snippet = s.snippet
            if s.highlight and s.highlight_at is not None:
                at, end = s.highlight_at, s.highlight_at + len(s.highlight)
                snippet = f"{snippet[:at]}«{snippet[at:end]}»{snippet[end:]}"
            lines += [f"  [{s.origin}] {s.cite}", f"      why: {s.why}",
                      f"      {' '.join(snippet.split())}"]
        lines.append("")
    return "\n".join(lines)
