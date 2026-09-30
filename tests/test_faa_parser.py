"""Tests for parsers/faa_pdf.py on known paragraphs of AC 43.13-1B.

Needs the PDF: python corpus/faa/fetch.py. Locally the PDF tests skip without it;
in CI (REQUIRE_PDF=1) a missing PDF fails instead.
"""
import os
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path[:0] = [str(ROOT / "parsers"), str(ROOT / "scripts")]

import check_chunks  # noqa: E402
import faa_pdf  # noqa: E402


# ---------------------------------------------------------------- no PDF needed

@pytest.mark.parametrize("kind, ids, want", [
    ("paragraphs", "2-9 through 2-12", ["2-9", "2-10", "2-11", "2-12"]),
    ("figures", "7-5 through 7-5b", ["7-5", "7-5a", "7-5b"]),
    ("paragraphs", "4-58g through 4-58n", [f"4-58{c}" for c in "ghijklmn"]),
    ("Tables", "11-7 and 11-8", ["11-7", "11-8"]),
    ("paragraph", "9-49b(2)(d)", ["9-49b(2)(d)"]),
    ("paragraph", "1-4 f", ["1-4f"]),
])
def test_expand_ids(kind, ids, want):
    assert faa_pdf.expand_ids(kind, ids) == want


@pytest.mark.parametrize("raw, want", [
    ("SAE ARP-1870", "SAE ARP1870"),
    ("ARP 1199", "SAE ARP1199"),
    ("ASTM-E-1417", "ASTM E1417"),
    ("MS 20365", "MS20365"),
    ("AN-666", "AN666"),
    ("MIL-W5088", "MIL-W-5088"),
    ("MIL–W–5088", "MIL-W-5088"),
])
def test_norm_spec(raw, want):
    assert faa_pdf.norm_spec(raw) == want


# ---------------------------------------------------------------- whole PDF

@pytest.fixture(scope="session")
def chunks():
    if not faa_pdf.PDF.exists():
        if os.environ.get("REQUIRE_PDF"):
            pytest.fail(f"{faa_pdf.PDF} missing; run corpus/faa/fetch.py")
        pytest.skip("FAA PDF not downloaded (python corpus/faa/fetch.py)")
    out, _ = faa_pdf.parse()
    return out


@pytest.fixture(scope="session")
def by_loc(chunks):
    return {c["loc"]["id"]: c for c in chunks}


def test_warning_6_152(by_loc):
    """Bold WARNING lead-in becomes its own warning chunk under its paragraph."""
    w = by_loc["6-152#warning-1"]
    assert w["type"] == "warning"
    assert w["extra"]["kind"] == "WARNING" and w["extra"]["parent"] == "6-152"
    assert w["text"].startswith("WARNING: Cuttings and small shavings from magnesium")
    assert "1/2 inch" in w["text"]
    assert w["loc"]["cite"] == "AC 43.13-1B ¶6-152 WARNING, p. 6-31"
    assert "Cuttings and small shavings" not in by_loc["6-152"]["text"]


def test_table_7_1_torque_values(by_loc):
    """Rule-less torque table keeps all five columns, the CAUTION and both series."""
    t = by_loc["table-7-1"]
    assert t["type"] == "table" and t["page"] == "7-9"
    assert "CAUTION" in t["text"]
    assert "FINE THREAD SERIES" in t["text"] and "COARSE THREAD SERIES" in t["text"]
    assert "8-36 | 12-15 | 7-9 | 20 | 12" in t["text"]
    assert "1/4-28 | 50-70 | 30-40 | 100 | 60" in t["text"]
    assert {"MS20364", "MS20365", "AN310", "AN320"} <= set(t["specs"])
    # table cells that look like paragraph numbers ("8-36") must not start paragraphs;
    # the only 8-36 is the real chapter 8 fuel-system paragraph
    assert by_loc["8-36"]["heading_path"][0].startswith("CHAPTER 8.")
    assert by_loc["8-36"]["extra"]["pdf_page"] > 390


def test_cross_refs_and_specs_7_144(by_loc):
    c = by_loc["7-144"]
    assert c["type"] == "text"
    assert c["refs_out"] == ["table-7-4", "figure-7-9"]
    assert {"MIL-W-87161", "MIL-W-83420"} <= set(c["specs"])
    assert c["loc"]["cite"] == "AC 43.13-1B ¶7-144, p. 7-28"
    assert c["heading_path"][-1] == "7-144. NONFLEXIBLE CABLES"


def test_ref_override_page_reference(by_loc):
    assert by_loc["7-2"]["refs_out"] == ["4-57f"]


def test_cleanup(chunks):
    pages = {c["extra"]["pdf_page"] for c in chunks}
    assert not pages & faa_pdf.DUP_PAGES, "duplicate pages 67-75 must be dropped"
    for c in chunks:
        assert "" not in c["text"] and "￧" not in c["text"], c["id"]
        assert "­" not in c["text"], c["id"]


def test_duplicate_paragraph_numbers_kept_apart(by_loc):
    """The source numbers 11-48..11-50 twice; the second set gets @p<pdf page>."""
    for n in ("11-48", "11-49", "11-50"):
        assert n in by_loc
        assert [k for k in by_loc if k.startswith(f"{n}@p")], n


def test_contract(chunks):
    errors, unresolved = check_chunks.check(chunks)
    assert errors == []
    assert 1150 <= len(chunks) <= 1350
    # known source defects (see build/unresolved_refs.md); anything new is a regression
    assert {u["ref"] for u in unresolved} <= {"figure-3-14", "figure-7-23", "figure-7-29"}
