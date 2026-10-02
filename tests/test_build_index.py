"""Tests for scripts/build_index.py (SPEC §5.1).

Two parts:
  - unit tests on a few made-up chunks with a fake embedder (fast, no model)
  - checks on the real FAA index, build/faa.sqlite. Locally they skip if it
    isn't built; in CI (REQUIRE_INDEX=1) a missing index fails instead.
"""
import json
import os
import sqlite3
import sys
from pathlib import Path

import numpy as np
import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path[:0] = [str(ROOT), str(ROOT / "scripts")]

import build_index  # noqa: E402
from locator import config  # noqa: E402

DOC = "Test Manual rev A"


def chunk(loc_id, text, **kw):
    c = {
        "id": f"t:{loc_id}",
        "doc": DOC,
        "loc": {"scheme": "faa-para", "id": loc_id, "cite": f"TM ¶{loc_id}"},
        "heading_path": kw.pop("heading_path", ["CHAPTER 1. BOLTS"]),
        "type": kw.pop("type", "text"),
        "text": text,
        "refs_out": kw.pop("refs_out", []),
    }
    c.update(kw)
    return c


CHUNKS = [
    chunk("1-1", "1-1. GENERAL. Bolts are installed dry.", refs_out=["table-1-1"],
          specs=["AN3"], page="1-1", extra={"pdf_page": 9}),
    chunk("table-1-1", "TABLE 1-1. Torque values for nuts.", type="table",
          specs=["AN3", "MS20365"], applies_to=["M100-A", "M100-B"]),
    chunk("1-2", "WARNING: Do not reuse safety wire.", type="warning",
          heading_path=["CHAPTER 1. BOLTS", "SECTION 2. SAFETYING"]),
]


def fake_embed(texts):
    """Deterministic 8-dim vectors, one per text."""
    return np.array([[len(t) + i for i in range(8)] for t in texts], dtype=np.float64)


@pytest.fixture
def db(tmp_path):
    out = tmp_path / "test.sqlite"
    build_index.build(CHUNKS, out, embed=fake_embed, model="fake-model")
    conn = sqlite3.connect(out)
    yield conn
    conn.close()


def q(conn, sql, *args):
    return conn.execute(sql, args).fetchall()


# ---------------------------------------------------------------- unit

def test_every_chunk_round_trips(db):
    rows = q(db, "SELECT id, doc, loc_scheme, loc_id, loc_cite, heading_path, type, text,"
                 " refs_out, specs, applies_to, page, extra FROM chunks ORDER BY rowid")
    assert len(rows) == len(CHUNKS)
    for row, c in zip(rows, CHUNKS):
        (id_, doc, scheme, loc_id, cite, heading, type_, text,
         refs, specs, applies, page, extra) = row
        assert (id_, doc, type_, text) == (c["id"], c["doc"], c["type"], c["text"])
        assert (scheme, loc_id, cite) == (c["loc"]["scheme"], c["loc"]["id"], c["loc"]["cite"])
        assert json.loads(heading) == c["heading_path"]
        assert json.loads(refs) == c["refs_out"]
        assert json.loads(specs) == c.get("specs", [])
        assert json.loads(applies) == c.get("applies_to", [])
        assert page == c.get("page")
        assert json.loads(extra) == c.get("extra", {})


def test_spec_lookup(db):
    rows = q(db, "SELECT c.loc_id FROM chunk_specs s JOIN chunks c ON c.id = s.chunk_id"
                 " WHERE s.spec = ? ORDER BY c.loc_id", "AN3")
    assert rows == [("1-1",), ("table-1-1",)]


def test_ref_lookup_finds_who_points_at_a_spot(db):
    rows = q(db, "SELECT chunk_id FROM chunk_refs WHERE ref = ?", "table-1-1")
    assert rows == [("t:1-1",)]


def test_applies_to_lookup(db):
    rows = q(db, "SELECT chunk_id, variant FROM chunk_applies_to ORDER BY variant")
    assert rows == [("t:table-1-1", "M100-A"), ("t:table-1-1", "M100-B")]


@pytest.mark.parametrize("query, want", [
    ("torque", "t:table-1-1"),           # body text
    ("safetying", "t:1-2"),              # heading_path only
    ("reuse", "t:1-2"),                  # "reuse" in text
])
def test_fts_finds_text_and_headings(db, query, want):
    rows = q(db, "SELECT c.id FROM chunks_fts JOIN chunks c ON c.rowid = chunks_fts.rowid"
                 " WHERE chunks_fts MATCH ?", query)
    assert [r[0] for r in rows] == [want]


def test_embeddings_are_float32_unit_vectors(db):
    rows = q(db, "SELECT chunk_id, vector FROM embeddings")
    assert {r[0] for r in rows} == {c["id"] for c in CHUNKS}
    for _, blob in rows:
        v = np.frombuffer(blob, dtype=np.float32)
        assert v.shape == (8,)
        assert np.isclose(np.linalg.norm(v), 1.0, atol=1e-5)


def test_meta_says_what_was_searched(db):
    meta = dict(q(db, "SELECT key, value FROM meta"))
    assert meta["doc"] == DOC
    assert meta["chunk_count"] == "3"
    assert meta["embed_model"] == "fake-model"
    assert meta["embed_dim"] == "8"


def test_rebuild_replaces_the_old_file(tmp_path):
    out = tmp_path / "test.sqlite"
    build_index.build(CHUNKS, out, embed=fake_embed, model="fake-model")
    build_index.build(CHUNKS[:1], out, embed=fake_embed, model="fake-model")
    with sqlite3.connect(out) as conn:
        assert q(conn, "SELECT count(*) FROM chunks") == [(1,)]


def test_rebuild_keeps_the_writers_marks(tmp_path):
    """Marks live in the index file (app/marks.py); a rebuild must carry them over."""
    from app.marks import Marks

    out = tmp_path / "test.sqlite"
    build_index.build(CHUNKS, out, embed=fake_embed, model="fake-model")
    with Marks(out) as m:
        rid = m.set("Made-up request.", CHUNKS[0]["loc"]["id"], "confirm", cite="TM",
                    tier="likely", origin="direct")
        before = m.export()
    build_index.build(CHUNKS, out, embed=fake_embed, model="fake-model")
    with Marks(out) as m:
        assert m.export() == before and rid in before


def test_rebuild_for_another_doc_refuses_to_drop_marks(tmp_path):
    from app.marks import Marks

    out = tmp_path / "test.sqlite"
    build_index.build(CHUNKS, out, embed=fake_embed, model="fake-model")
    with Marks(out) as m:
        m.set("Made-up request.", CHUNKS[0]["loc"]["id"], "confirm", cite="TM")
    other = [{**c, "doc": "Other manual"} for c in CHUNKS]
    with pytest.raises(ValueError, match="marks"):
        build_index.build(other, out, embed=fake_embed, model="fake-model")
    with Marks(out) as m:
        assert m.export()   # old file untouched


def test_one_self_contained_file(tmp_path):
    out = tmp_path / "test.sqlite"
    build_index.build(CHUNKS, out, embed=fake_embed, model="fake-model")
    assert [p.name for p in tmp_path.iterdir()] == ["test.sqlite"]  # no -wal / -journal


def test_refuses_chunks_that_fail_the_contract(tmp_path):
    bad = [CHUNKS[0], {**CHUNKS[1], "id": CHUNKS[0]["id"]}]  # duplicate id
    with pytest.raises(ValueError, match="duplicate id"):
        build_index.build(bad, tmp_path / "x.sqlite", embed=fake_embed, model="fake-model")
    assert not (tmp_path / "x.sqlite").exists()


def test_refuses_more_than_one_doc(tmp_path):
    other = {**CHUNKS[1], "doc": "Other manual"}
    with pytest.raises(ValueError, match="one doc"):
        build_index.build([CHUNKS[0], other], tmp_path / "x.sqlite",
                          embed=fake_embed, model="fake-model")


def test_repeated_variant_is_looked_up_once(tmp_path):
    """The schema allows repeats in applies_to; the lookup table keeps each once."""
    c = {**CHUNKS[1], "applies_to": ["M100-A", "M100-A"]}
    out = tmp_path / "x.sqlite"
    build_index.build([c], out, embed=fake_embed, model="fake-model")
    with sqlite3.connect(out) as conn:
        assert q(conn, "SELECT variant FROM chunk_applies_to") == [("M100-A",)]
        assert json.loads(q(conn, "SELECT applies_to FROM chunks")[0][0]) == ["M100-A", "M100-A"]


def test_embed_text_leads_with_headings():
    assert build_index.embed_text(CHUNKS[2]) == (
        "CHAPTER 1. BOLTS > SECTION 2. SAFETYING\nWARNING: Do not reuse safety wire.")


# ---------------------------------------------------------------- real FAA index

INDEX = ROOT / "build/faa.sqlite"
JSONL = ROOT / "build/faa_chunks.jsonl"
MODEL_DIMS = {"BAAI/bge-small-en-v1.5": 384, "BAAI/bge-base-en-v1.5": 768}


@pytest.fixture(scope="module")
def faa():
    if not INDEX.exists():
        if os.environ.get("REQUIRE_INDEX"):
            pytest.fail(f"{INDEX} missing; run scripts/build_index.py")
        pytest.skip("FAA index not built (python scripts/build_index.py build/faa_chunks.jsonl)")
    conn = sqlite3.connect(INDEX)
    yield conn
    conn.close()


def test_faa_row_count_matches_jsonl(faa):
    n = sum(1 for line in JSONL.open() if line.strip())
    assert q(faa, "SELECT count(*) FROM chunks") == [(n,)]
    assert q(faa, "SELECT count(*) FROM embeddings") == [(n,)]
    assert dict(q(faa, "SELECT key, value FROM meta"))["chunk_count"] == str(n)


def test_faa_fts_torque_finds_table_7_1(faa):
    rows = q(faa, "SELECT c.loc_id FROM chunks_fts JOIN chunks c ON c.rowid = chunks_fts.rowid"
                  " WHERE chunks_fts MATCH 'torque'")
    assert "table-7-1" in {r[0] for r in rows}


def test_faa_spec_lookup_finds_7_144(faa):
    rows = q(faa, "SELECT c.loc_id FROM chunk_specs s JOIN chunks c ON c.id = s.chunk_id"
                  " WHERE s.spec = 'MIL-W-87161'")
    assert "7-144" in {r[0] for r in rows}


def test_faa_embeddings_have_model_dims(faa):
    meta = dict(q(faa, "SELECT key, value FROM meta"))
    assert meta["embed_model"] == config.EMBED_MODEL
    dim = MODEL_DIMS[config.EMBED_MODEL]
    assert meta["embed_dim"] == str(dim)
    sizes = q(faa, "SELECT DISTINCT length(vector) FROM embeddings")
    assert sizes == [(dim * 4,)]  # float32
