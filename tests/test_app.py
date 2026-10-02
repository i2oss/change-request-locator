"""Tests for the local Flask app (SPEC §5.4), on the made-up manual with the fake embedder.

Made-up requests only (SPEC §6.4).
"""
import json

import numpy as np
import pytest

from app import create_app, find_loc
from app.marks import request_id
import build_index
from conftest import DIM, MANUAL, chunk, fake_embed
from locator import search

REQUEST = "Brake lining spec MIL-B-99001 is replaced by MIL-B-99002."


def no_meaning(texts):
    """Points nowhere, so only exact and keyword search find spots. (The fake embedder
    finds every chunk of the small manual a little similar, which leaves nothing missed.)"""
    return np.zeros((len(texts), DIM), dtype=np.float32)


@pytest.fixture
def app(manual_db):
    return create_app(manual_db, embed=no_meaning)


@pytest.fixture
def client(app):
    return app.test_client()


def page(client, text=None):
    resp = client.get("/", query_string={"request": text} if text else None)
    assert resp.status_code == 200
    return resp.get_data(as_text=True)


def mark(client, loc_id, verdict, **kw):
    return client.post("/marks", json={"request": REQUEST, "loc_id": loc_id, "verdict": verdict,
                                       **kw})


def test_empty_page_says_what_will_be_searched(client):
    html = page(client)
    assert "Made-up Brake Manual rev A, 12 chunks" in html
    assert '<textarea name="request"' in html
    assert "Likely" not in html


def test_request_shows_tiered_spots_with_citation_why_and_highlight(client):
    html = page(client, REQUEST)
    assert "Made-up Brake Manual rev A, 12 chunks" in html
    likely, check = html.split('id="tier-check"')
    # exact spec hits are direct spots in Likely
    assert "MBM ¶3-1" in likely and "MBM ¶table-3-1" in likely
    assert "names spec MIL-B-99001" in likely
    assert "<mark>MIL-B-99001</mark>" in likely
    assert 'data-origin="direct"' in likely
    # 3-2 points at 3-1: a ripple, labelled as one
    assert 'data-loc="3-2"' in html and "points to ¶3-1" in html
    assert 'data-origin="ripple"' in html


def test_page_never_shows_scores(client):
    assert "score" not in page(client, REQUEST).lower()


def test_request_with_no_spots_says_what_was_searched(client):
    html = page(client, "zzz qqq")
    assert "No spots found in Made-up Brake Manual rev A, 12 chunks" in html


def test_text_from_the_manual_is_escaped(manual_db, tmp_path):
    import sqlite3
    with sqlite3.connect(manual_db) as conn:
        conn.execute("UPDATE chunks SET text = text || ' <script>x</script>' WHERE loc_id = '3-1'")
    html = page(create_app(manual_db, embed=fake_embed).test_client(), REQUEST)
    assert "<script>x</script>" not in html and "&lt;script&gt;" in html


def test_mark_a_spot_and_see_it_after_reload(client):
    resp = mark(client, "3-1", "confirm", tier="likely", origin="direct")
    assert resp.status_code == 200
    assert resp.get_json() == {"request_id": request_id(REQUEST), "loc_id": "3-1",
                               "cite": "MBM ¶3-1", "verdict": "confirm"}
    html = page(client, REQUEST)
    assert 'data-loc="3-1" data-tier="likely" data-origin="direct" data-verdict="confirm"' in html


def test_clear_a_mark(client):
    mark(client, "3-1", "confirm", tier="likely", origin="direct")
    assert mark(client, "3-1", "clear").status_code == 200
    assert 'data-loc="3-1" data-tier="likely" data-origin="direct" data-verdict=""' in page(
        client, REQUEST)


def test_add_missed_by_loc_id(client):
    resp = mark(client, "7-1", "add-missed")
    assert resp.get_json()["cite"] == "MBM ¶7-1"
    html = page(client, REQUEST)
    assert 'id="missed"' in html and "MBM ¶7-1" in html.split('id="missed"')[1]


@pytest.mark.parametrize("typed, loc", [("¶7-1", "7-1"), (" 7-1 ", "7-1"), ("para 7-1", "7-1"),
                                        ("Figure 3-1", "figure-3-1"), ("table 3-1", "table-3-1"),
                                        ("7-9", None)])
def test_find_loc_takes_the_ways_a_writer_types_a_spot(manual_db, typed, loc):
    with search.Index(manual_db) as index:
        assert [c.loc_id for c in find_loc(index, typed)] == ([loc] if loc else [])


@pytest.fixture
def twice_db(tmp_path):
    """The made-up manual with a second ¶7-1 further on, keyed like the FAA parser
    keys a number the source uses twice."""
    second = chunk("7-1@p40", "7-1. DRYING. Dry the landing gear with clean air.",
                   heading_path=["CHAPTER 7. CLEANING"])
    second["loc"]["cite"] = "MBM ¶(second) 7-1"
    out = tmp_path / "twice.sqlite"
    build_index.build(MANUAL + [second], out, embed=fake_embed, model="fake-model")
    return out


def test_find_loc_finds_every_spot_with_a_number_used_twice(twice_db):
    with search.Index(twice_db) as index:
        assert [c.loc_id for c in find_loc(index, "7-1")] == ["7-1", "7-1@p40"]
        assert [c.loc_id for c in find_loc(index, "7-1@p40")] == ["7-1@p40"]


def test_add_missed_with_a_number_used_twice_asks_which(twice_db):
    client = create_app(twice_db, embed=no_meaning).test_client()
    resp = mark(client, "7-1", "add-missed")
    assert resp.status_code == 400
    error = resp.get_json()["error"]
    assert "MBM ¶7-1" in error and "MBM ¶(second) 7-1" in error and "7-1@p40" in error
    assert mark(client, "7-1@p40", "add-missed").get_json()["cite"] == "MBM ¶(second) 7-1"


def test_add_missed_reads_a_typed_spot(client):
    assert mark(client, "para 7-1", "add-missed").get_json()["loc_id"] == "7-1"


def test_add_missed_unknown_spot_is_refused(client):
    resp = mark(client, "99-9", "add-missed")
    assert resp.status_code == 400
    assert "99-9" in resp.get_json()["error"]


def test_add_missed_refuses_a_spot_already_listed(client):
    resp = mark(client, "3-1", "add-missed")
    assert resp.status_code == 400
    assert "already listed" in resp.get_json()["error"]


def test_bad_verdict_is_refused(client):
    assert mark(client, "3-1", "maybe").status_code == 400


def test_export_downloads_jsonl(client):
    mark(client, "3-1", "confirm", tier="likely", origin="direct")
    mark(client, "7-1", "add-missed")
    resp = client.get("/marks.jsonl")
    assert resp.status_code == 200
    assert "attachment" in resp.headers["Content-Disposition"]
    recs = [json.loads(l) for l in resp.get_data(as_text=True).splitlines()]
    assert [(r["loc_id"], r["verdict"]) for r in recs] == [("3-1", "confirm"), ("7-1", "add-missed")]


def test_export_one_request(client):
    mark(client, "3-1", "confirm", tier="likely", origin="direct")
    client.post("/marks", json={"request": "Tire pressure is now 48 psi.", "loc_id": "6-1",
                                "verdict": "confirm", "tier": "likely", "origin": "direct"})
    resp = client.get("/marks.jsonl", query_string={"request_id": request_id(REQUEST)})
    assert [json.loads(l)["loc_id"] for l in resp.get_data(as_text=True).splitlines()] == ["3-1"]


def test_import_marks_command_round_trips(app, client, tmp_path):
    mark(client, "3-1", "confirm", tier="likely", origin="direct")
    exported = client.get("/marks.jsonl").get_data(as_text=True)
    path = tmp_path / "marks.jsonl"
    path.write_text(exported)
    mark(client, "3-1", "clear")

    result = app.test_cli_runner().invoke(args=["import-marks", str(path)])
    assert result.exit_code == 0, result.output
    assert "1 marks" in result.output
    assert client.get("/marks.jsonl").get_data(as_text=True) == exported


def test_missing_index_says_how_to_build_it(tmp_path):
    resp = create_app(tmp_path / "none.sqlite", embed=fake_embed).test_client().get("/")
    assert resp.status_code == 500
    assert "scripts/build_index.py" in resp.get_data(as_text=True)
