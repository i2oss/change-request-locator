"""Smoke tests: the baseline locator on the real FAA index, build/faa.sqlite.

Every request here is made up. None is taken from the 2024 FAA edits: those
become the eval set, and tuning on them spoils it (SPEC §6.4). These only check
that the pipeline hangs together on real data; they are not a score.

Locally they skip if the index isn't built; in CI (REQUIRE_INDEX=1) they fail instead.
"""
import os
import re
from pathlib import Path

import pytest

from locator import search, tiers

INDEX = Path(__file__).resolve().parent.parent / "build/faa.sqlite"


@pytest.fixture(scope="module")
def faa():
    if not INDEX.exists():
        if os.environ.get("REQUIRE_INDEX"):
            pytest.fail(f"{INDEX} missing; run scripts/build_index.py")
        pytest.skip("FAA index not built (python scripts/build_index.py build/faa_chunks.jsonl)")
    with search.Index(INDEX) as index:
        yield index, search.query_embedder(index.meta["embed_model"])


def run(faa, text):
    index, embed = faa
    t = tiers.tier(search.locate(index, search.read_request(text), embed=embed))
    return {s.loc_id: s for s in t.likely}, {s.loc_id: s for s in t.check}


def test_spec_swap_finds_7_144_and_its_ripples(faa):
    likely, check = run(faa, "Flexible cable spec MIL-W-83420 is replaced by MRS-C-200. "
                             "Update every place it appears.")
    for loc in ("7-141", "7-142", "7-143", "7-144"):
        assert likely[loc].origin == "direct"
        assert likely[loc].why == "names spec MIL-W-83420"
    ripples = {loc: s for loc, s in {**likely, **check}.items() if s.origin == "ripple"}
    assert ripples["table-7-4"].why.startswith("same spec MIL-W-87161 as ")


def test_named_table_brings_the_paragraphs_pointing_at_it(faa):
    likely, _ = run(faa, "Table 7-4 gets a new row for 5/16 inch nonflexible cable.")
    assert likely["table-7-4"].origin == "direct"
    for loc in ("7-144", "7-145", "7-146"):
        assert likely[loc].origin == "ripple"
        assert likely[loc].why.startswith("points to table 7-4")


def test_words_alone_find_the_subject(faa):
    likely, _ = run(faa, "Clarify how turnbuckle safety wire is wrapped around the barrel.")
    assert likely and all(s.loc_id.startswith("7-") for s in likely.values())


def test_output_shows_no_scores(faa):
    index, embed = faa
    request = search.read_request("Raise the cable proof test load from 60 percent to 65 percent.")
    t = tiers.tier(search.locate(index, request, embed=embed))
    out = tiers.render(index.scope(), "", t)
    assert re.match(r"Searched: AC 43\.13-1B .*, [\d,]+ chunks\n", out)
    assert not re.search(r"score|\b0\.\d{3,}|\d\.\d+e-", out, re.I)
