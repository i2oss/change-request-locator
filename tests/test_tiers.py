"""Tests for locator/tiers.py and the CLI (SPEC §2, §5.2 step 4, baseline).

Made-up requests only (SPEC §6.4).
"""
import dataclasses
import re

import pytest

from conftest import MANUAL, fake_embed
from locator import __main__ as cli
from locator import search, tiers
from locator.search import Candidate, Chunk, Reason


def chunk_of(loc_id):
    c = next(c for c in MANUAL if c["loc"]["id"] == loc_id)
    return Chunk(0, c["id"], loc_id, c["loc"]["cite"], c["type"], c["text"],
                 tuple(c["heading_path"]), tuple(c.get("specs", [])),
                 tuple(c.get("applies_to", [])))


def direct(loc_id, rank, *reasons, exact=False):
    return Candidate(chunk_of(loc_id), "direct", rank, list(reasons), exact=exact)


def ripple(loc_id, rank, reason, seed):
    return Candidate(chunk_of(loc_id), "ripple", rank, [reason], seed=f"m:{seed}")


def locs(spots):
    return [s.loc_id for s in spots]


# ---------------------------------------------------------------- tiers

def test_exact_hits_are_likely_and_search_only_spots_check_these():
    t = tiers.tier([
        direct("3-1", 0, Reason("spec", "MIL-B-99001"), exact=True),
        direct("3-3", 1, Reason("words", "brake")),
    ])
    assert locs(t.likely) == ["3-1"] and locs(t.check) == ["3-3"]


def test_without_exact_hits_the_top_search_spots_are_likely(monkeypatch):
    monkeypatch.setattr(tiers, "LIKELY_TOP", 2)
    cands = [direct(loc, i, Reason("words", "brake"))
             for i, loc in enumerate(["3-3", "3-3f", "3-2"])]
    t = tiers.tier(cands)
    assert locs(t.likely) == ["3-3", "3-3f"] and locs(t.check) == ["3-2"]


def test_strong_ripples_of_likely_spots_are_likely():
    t = tiers.tier([
        direct("3-1", 0, Reason("spec", "MIL-B-99001"), exact=True),
        ripple("3-2", 1, Reason("points_to", via="3-1"), seed="3-1"),
    ])
    assert locs(t.likely) == ["3-1", "3-2"]


def test_weak_ripples_and_ripples_of_check_these_spots_are_check_these():
    t = tiers.tier([
        direct("3-2", 0, Reason("loc", "3-2"), exact=True),
        direct("3-3", 1, Reason("words", "brake")),
        ripple("5-4", 2, Reason("same_spec", "MIL-D-99007A", via="3-2"), seed="3-2"),
        ripple("6-1-r", 3, Reason("variant", "Retractable", via="6-1"), seed="3-2"),
        ripple("3-3f", 4, Reason("points_to", via="3-3"), seed="3-3"),
    ])
    assert locs(t.likely) == ["3-2"]
    assert locs(t.check) == ["3-3", "5-4", "6-1-r", "3-3f"]


def test_tier_keeps_origin_labels():
    t = tiers.tier([
        direct("3-1", 0, Reason("spec", "MIL-B-99001"), exact=True),
        ripple("3-2", 1, Reason("points_to", via="3-1"), seed="3-1"),
    ])
    assert [s.origin for s in t.likely] == ["direct", "ripple"]


# ---------------------------------------------------------------- why lines

@pytest.mark.parametrize("reasons, why", [
    ([Reason("spec", "MIL-B-99001")], "names spec MIL-B-99001"),
    ([Reason("loc", "3-1")], "is ¶3-1, named in the request"),
    ([Reason("loc", "table-3-1")], "is table 3-1, named in the request"),
    ([Reason("value", "0.10 inch")], "states 0.10 inch"),
    ([Reason("words", "brake, lining")], "mentions brake, lining"),
    ([Reason("words", "")], "matches words in the request"),
    ([Reason("meaning")], "close in meaning to the request"),
    ([Reason("points_to", via="11-48")], "points to ¶11-48"),
    ([Reason("same_spec", "MIL-W-5088", via="11-48")], "same spec MIL-W-5088 as ¶11-48"),
    ([Reason("same_warning", via="3-2#warning-1")], "repeats the warning at ¶3-2 warning 1"),
    ([Reason("variant", "Retractable", via="6-1")], "Retractable version of ¶6-1"),
    ([Reason("spec", "MIL-B-99001"), Reason("value", "0.10 inch"), Reason("meaning")],
     "names spec MIL-B-99001; states 0.10 inch"),
])
def test_why_lines_come_from_templates(reasons, why):
    assert tiers.why(reasons) == why


# ---------------------------------------------------------------- snippets

def test_snippet_highlights_what_was_found():
    s = tiers.tier([direct("table-3-1", 0, Reason("value", "0.10 inch"))]).likely[0]
    assert s.highlight == "0.10 inch" and "0.10 inch" in s.snippet


def test_long_snippets_are_cut_around_the_highlight(monkeypatch):
    monkeypatch.setattr(tiers, "SNIPPET_CHARS", 30)
    s = tiers.tier([direct("3-1", 0, Reason("value", "0.10 inch"))]).likely[0]
    assert s.snippet.startswith("…") and "0.10 inch" in s.snippet
    assert len(s.snippet) <= 32


def test_spots_carry_no_scores():
    fields = {f.name for f in dataclasses.fields(tiers.Spot)}
    assert not fields & {"score", "rank", "rrf", "similarity", "bm25"}


# ---------------------------------------------------------------- CLI

@pytest.fixture
def run_cli(manual_db, monkeypatch, capsys):
    def run(text, embed=fake_embed):
        monkeypatch.setattr(search, "query_embedder", lambda model: embed)
        cli.main([text, "--db", str(manual_db)])
        return capsys.readouterr().out
    return run


def test_cli_prints_scope_then_tiered_spots(run_cli):
    out = run_cli("Replace MIL-B-99001 with MIL-B-99002.", embed=lambda texts: 0 * fake_embed(texts))
    lines = out.splitlines()
    assert lines[0] == "Searched: Made-up Brake Manual rev A, 12 chunks"
    assert lines.index("Likely") < lines.index("Check these")
    assert "  [direct] MBM ¶3-1" in lines
    assert "      why: names spec MIL-B-99001" in lines
    assert "«MIL-B-99001»" in out
    assert "  [ripple] MBM ¶3-2" in lines


def test_cli_never_shows_numeric_scores(run_cli):
    out = run_cli("Brake lining wear, disc warping and tire pressure.")
    assert not re.search(r"score|\b0\.\d{3,}|\d\.\d+e-", out, re.I)


def test_cli_says_when_nothing_was_found(run_cli):
    out = run_cli("please update", embed=lambda texts: 0 * fake_embed(texts))
    assert "No spots found in Made-up Brake Manual rev A, 12 chunks." in out
