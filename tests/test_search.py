"""Tests for locator/search.py (SPEC §5.2 steps 1-3, baseline).

Made-up requests only. Requests from the 2024 FAA edits are the eval set;
tuning on them spoils it (SPEC §6.4).
"""
import pytest

from conftest import fake_embed
from locator import search
from locator.search import read_request


# ---------------------------------------------------------------- 1. read the request

def test_reads_spec_numbers_in_one_written_form():
    r = read_request("Replace MIL-W5088 with MIL-B-99002 and drop MS 21919.")
    assert r.specs == ("MIL-W-5088", "MIL-B-99002", "MS21919")


@pytest.mark.parametrize("text, refs", [
    ("Revise paragraph 7-144 and table 7-4.", ("7-144", "table-7-4")),
    ("Figure 7-9 callout 3 is wrong.", ("figure-7-9",)),
    ("See paragraphs 3-1 through 3-3.", ("3-1", "3-2", "3-3")),
    ("Fix ¶3-2 and para. 5-4b.", ("3-2", "5-4")),  # sub-para -> its paragraph
])
def test_reads_paragraph_table_and_figure_refs(text, refs):
    assert read_request(text).refs == refs


def test_reads_values_but_not_numbers_inside_specs_or_refs():
    r = read_request("In paragraph 3-1 change the MIL-B-99001 wear limit from 0.10 inch "
                     "to 3/32 inch, torque 150 in-lb, and tires to 45 psi.")
    assert r.values == ("0.10 inch", "3/32 inch", "150 in-lb", "45 psi")


def test_terms_are_the_meaningful_words():
    r = read_request("Please update paragraph 3-1: the brake lining spec MIL-B-99001 changes.")
    assert r.terms == ("brake", "lining")


def test_unknown_codes_stay_whole_as_terms():
    """Codes SPEC_RE doesn't know (made-up specs, part numbers) are still searched as words."""
    r = read_request("Spec MIL-W-83420 is replaced by MRS-C-200 wherever it appears.")
    assert r.specs == ("MIL-W-83420",)
    assert r.terms == ("mrs-c-200",)


def test_empty_request_reads_nothing():
    r = read_request("   ")
    assert (r.specs, r.refs, r.values, r.terms) == ((), (), (), ())


# ---------------------------------------------------------------- 2-3. search and ripples

import numpy as np  # noqa: E402

from conftest import DIM  # noqa: E402


def no_meaning(texts):
    """An embedder that points nowhere, so only exact and keyword search find spots."""
    return np.zeros((len(texts), DIM), dtype=np.float32)


@pytest.fixture
def index(manual_db):
    with search.Index(manual_db) as ix:
        yield ix


def locate(index, text, embed=no_meaning):
    return {c.chunk.loc_id: c for c in search.locate(index, read_request(text), embed=embed)}


def kinds(c):
    return [r.kind for r in c.reasons]


def test_scope_says_what_was_searched(index):
    assert index.scope() == "Made-up Brake Manual rev A, 12 chunks"


def test_spec_lookup_finds_every_chunk_stating_it(index):
    found = locate(index, "Replace MIL-B-99001 with MIL-B-99002.")
    for loc in ("3-1", "table-3-1"):
        assert found[loc].origin == "direct" and found[loc].exact
        assert found[loc].reasons[0] == search.Reason("spec", "MIL-B-99001")


def test_spec_lookup_ignores_a_revision_letter(index):
    found = locate(index, "MIL-D-99007 is cancelled.")
    assert {"3-2", "5-4"} <= {loc for loc, c in found.items() if c.exact}


def test_named_paragraph_brings_its_parts_and_warnings(index):
    found = locate(index, "Revise paragraph 3-3.")
    assert {"3-3", "3-3f"} <= {loc for loc, c in found.items() if c.exact}
    found = locate(index, "Revise paragraph 3-2.")
    assert found["3-2#warning-1"].exact and kinds(found["3-2#warning-1"]) == ["loc"]


def test_exact_hits_come_first_and_are_always_kept(index, monkeypatch):
    monkeypatch.setattr(search, "FUSED_KEEP", 0)
    found = search.locate(index, read_request("Brake disc MIL-B-99001 change."), embed=no_meaning)
    directs = [c for c in found if c.origin == "direct"]
    assert [c.chunk.loc_id for c in directs] == ["3-1", "table-3-1"]
    assert [c.rank for c in found] == sorted(c.rank for c in found)


def test_keyword_search_finds_words(index):
    found = locate(index, "Brake bleeding: bleed until no bubbles.")
    assert found["3-3f"].origin == "direct" and not found["3-3f"].exact
    assert found["3-3f"].reasons[0].kind == "words"
    assert "bubbles" in found["3-3f"].reasons[0].detail


def test_keyword_search_finds_codes(index):
    found = locate(index, "Part PN-4471-2 is superseded.")
    assert found["figure-3-1"].reasons[0].kind == "words"
    assert "pn-4471-2" in found["figure-3-1"].reasons[0].detail.split(", ")


def test_value_search_finds_the_value(index):
    found = locate(index, "Wear limit goes from 0.10 inch to 0.08 inch.")
    assert search.Reason("value", "0.10 inch") in found["table-3-1"].reasons


def test_vector_search_finds_meaning_without_shared_words(index):
    def points_at_cleaning(texts):
        return fake_embed(["wash landing gear mild soap water"] * len(texts))
    found = locate(index, "Scrub the undercarriage.", embed=points_at_cleaning)
    assert kinds(found["7-1"]) == ["meaning"]


def test_fused_spots_are_capped(index, monkeypatch):
    monkeypatch.setattr(search, "FUSED_KEEP", 2)
    found = locate(index, "brake disc lining wheel tire", embed=fake_embed)
    assert sum(1 for c in found.values() if c.origin == "direct") == 2


def test_ripple_points_to_a_hit(index):
    found = locate(index, "Replace MIL-B-99001 with MIL-B-99002.")
    assert found["3-2"].origin == "ripple"
    assert found["3-2"].reasons[0] == search.Reason("points_to", via="3-1")
    assert found["3-2"].seed == "m:3-1"


def test_ripple_shares_a_spec_with_a_hit(index):
    found = locate(index, "Revise paragraph 3-2.")
    # The why names the spec as the ripple itself states it.
    assert found["5-4"].reasons == [search.Reason("same_spec", "MIL-D-99007", via="3-2")]


def test_ripple_repeats_a_hit_warning(index):
    found = locate(index, "Revise paragraph 3-2.")
    assert found["5-4#warning-1"].reasons[0] == search.Reason(
        "same_warning", via="3-2#warning-1")


def test_ripple_is_an_applicability_split(index):
    found = locate(index, "Revise paragraph 6-1.")
    assert found["6-1-r"].reasons == [search.Reason("variant", "Retractable", via="6-1")]


def test_only_incoming_refs_are_ripples(index):
    """3-2 points to 3-1; that makes 3-2 a ripple of 3-1, not the other way round."""
    assert "3-1" not in locate(index, "Revise paragraph 3-2.")


def test_a_common_spec_is_not_a_ripple_signal(index, monkeypatch):
    monkeypatch.setattr(search, "SPEC_FANOUT", 1)
    assert "5-4" not in locate(index, "Revise paragraph 3-2.")


def test_a_direct_spot_is_never_also_listed_as_a_ripple(index):
    found = search.locate(index, read_request("Paragraphs 3-1 and 3-2."), embed=no_meaning)
    ids = [c.chunk.id for c in found]
    assert len(ids) == len(set(ids))
    assert {c.chunk.loc_id: c.origin for c in found}["3-2"] == "direct"


def test_ripples_only_hang_off_the_top_spots(index, monkeypatch):
    monkeypatch.setattr(search, "RIPPLE_SEEDS", 0)
    found = locate(index, "brake disc warping")   # no exact hits; 3-2 found by words
    assert found["3-2"].origin == "direct"
    assert all(c.origin == "direct" for c in found.values())


def test_nothing_to_search_finds_nothing(index):
    assert search.locate(index, read_request("please update"), embed=no_meaning) == []


def test_a_search_spot_that_is_a_ripple_is_listed_as_the_ripple(index, monkeypatch):
    """A structural link says more than a search rank; the search reason stays on as extra."""
    monkeypatch.setattr(search, "RIPPLE_SEEDS", 0)
    found = locate(index, "MIL-B-99001 brake disc warping")   # 3-2 found by words too
    assert found["3-2"].origin == "ripple"
    assert kinds(found["3-2"]) == ["points_to", "words"]


def test_a_top_search_spot_that_is_a_ripple_of_an_exact_hit_is_that_ripple(index):
    found = locate(index, "Revise paragraph 3-1 on brake disc warping.")   # 3-2 tops the words
    assert found["3-2"].origin == "ripple"
    assert found["3-2"].reasons[0] == search.Reason("points_to", via="3-1")


def test_top_search_spots_stay_direct_beside_each_other(index):
    """Without an exact hit, a top search spot is not demoted by another search spot."""
    found = locate(index, "brake lining disc warping", embed=fake_embed)
    assert found["3-1"].origin == found["3-2"].origin == "direct"


def test_spec_reason_names_the_revision_the_chunk_states(index):
    found = locate(index, "MIL-D-99007B is cancelled.")
    assert found["3-2"].reasons[0] == search.Reason("spec", "MIL-D-99007A")
    assert found["5-4"].reasons[0] == search.Reason("spec", "MIL-D-99007")


def test_lowercase_revision_letters_are_revisions_too():
    assert search.spec_key("TSO-C91a") == search.spec_key("TSO-C91") == "TSO-C91"
    assert search.spec_key("MIL-W-5088L") == "MIL-W-5088"


def test_value_written_differently_still_gets_a_reason(index):
    """FTS matches 0.10-inch / 0.10 inches; the reason must not go missing."""
    found = locate(index, "Wear limit goes from 0.10 inches to 0.08 inches.")
    assert search.Reason("value", "0.10 inches") in found["table-3-1"].reasons


def test_every_candidate_has_a_reason(index):
    for text in ("Wear limit 0.10 inches.", "brake 12 psi", "Revise paragraph 3-2."):
        for c in search.locate(index, read_request(text), embed=fake_embed):
            assert c.reasons, c.chunk.loc_id



def test_a_ripple_keeps_every_link_strongest_first(index):
    """3-2 shares a spec with 5-4 (found first) and points at 3-1: the strong link leads."""
    found = locate(index, "Revise paragraphs 5-4 and 3-1.")
    assert found["3-2"].origin == "ripple"
    assert found["3-2"].reasons == [search.Reason("points_to", via="3-1"),
                                    search.Reason("same_spec", "MIL-D-99007A", via="5-4")]
    assert found["3-2"].reasons[0].seed == "m:3-1"
