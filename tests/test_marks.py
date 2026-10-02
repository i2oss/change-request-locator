"""Tests for app/marks.py: the writer's marks, saved in the corpus SQLite (SPEC §2, §5.2 step 5).

Made-up requests only (SPEC §6.4).
"""
import json

import pytest

from app import marks
from app.marks import Marks

REQUEST = "Brake lining spec MIL-B-99001 is replaced by MIL-B-99002."


@pytest.fixture
def store(manual_db):
    with Marks(manual_db) as m:
        yield m


def test_a_request_keeps_one_id_however_it_is_spaced():
    assert marks.request_id(REQUEST) == marks.request_id(f"  {REQUEST.replace(' ', '\n  ')} \n")
    assert marks.request_id(REQUEST) != marks.request_id(REQUEST + " Also 3-2.")
    assert marks.request_id(REQUEST).startswith("cr-")


def test_mark_a_spot(store):
    rid = store.set(REQUEST, "3-1", "confirm", cite="MBM ¶3-1", tier="likely", origin="direct")
    got = store.for_request(rid)
    assert list(got) == ["3-1"]
    assert (got["3-1"]["verdict"], got["3-1"]["tier"], got["3-1"]["origin"]) == (
        "confirm", "likely", "direct")


def test_a_new_verdict_replaces_the_old_one(store):
    rid = store.set(REQUEST, "3-1", "confirm", cite="MBM ¶3-1", tier="likely", origin="direct")
    store.set(REQUEST, "3-1", "reject", cite="MBM ¶3-1", tier="likely", origin="direct")
    assert store.for_request(rid)["3-1"]["verdict"] == "reject"


def test_clear_a_mark(store):
    rid = store.set(REQUEST, "3-1", "confirm", cite="MBM ¶3-1", tier="likely", origin="direct")
    store.clear(rid, "3-1")
    assert store.for_request(rid) == {}


def test_add_missed_has_no_tier_or_origin(store):
    rid = store.set(REQUEST, "7-1", "add-missed", cite="MBM ¶7-1")
    m = store.for_request(rid)["7-1"]
    assert (m["verdict"], m["tier"], m["origin"]) == ("add-missed", None, None)


def test_only_the_three_verdicts(store):
    with pytest.raises(ValueError, match="maybe"):
        store.set(REQUEST, "3-1", "maybe", cite="MBM ¶3-1")


def test_marks_are_kept_per_request(store):
    a = store.set(REQUEST, "3-1", "confirm", cite="MBM ¶3-1", tier="likely", origin="direct")
    b = store.set("Tire pressure for fixed gear is now 48 psi.", "6-1", "confirm",
                  cite="MBM ¶6-1", tier="likely", origin="direct")
    assert list(store.for_request(a)) == ["3-1"] and list(store.for_request(b)) == ["6-1"]


def test_marks_survive_a_restart(manual_db):
    with Marks(manual_db) as m:
        rid = m.set(REQUEST, "3-1", "confirm", cite="MBM ¶3-1", tier="likely", origin="direct")
    with Marks(manual_db) as m:
        assert m.for_request(rid)["3-1"]["verdict"] == "confirm"


def test_export_is_one_json_mark_per_line(store):
    store.set(REQUEST, "3-1", "confirm", cite="MBM ¶3-1", tier="likely", origin="direct")
    store.set(REQUEST, "7-1", "add-missed", cite="MBM ¶7-1")
    lines = store.export().splitlines()
    assert len(lines) == 2
    rec = json.loads(lines[0])
    assert rec | {"marked_at": "-"} == {
        "request_id": marks.request_id(REQUEST), "request": REQUEST,
        "doc": "Made-up Brake Manual rev A", "loc_id": "3-1", "cite": "MBM ¶3-1",
        "verdict": "confirm", "tier": "likely", "origin": "direct", "marked_at": "-"}


def test_export_one_request(store):
    rid = store.set(REQUEST, "3-1", "confirm", cite="MBM ¶3-1", tier="likely", origin="direct")
    store.set("Tire pressure for fixed gear is now 48 psi.", "6-1", "confirm",
              cite="MBM ¶6-1", tier="likely", origin="direct")
    assert [json.loads(l)["loc_id"] for l in store.export(rid).splitlines()] == ["3-1"]


def test_export_round_trips(manual_db, tmp_path):
    with Marks(manual_db) as m:
        m.set(REQUEST, "3-1", "confirm", cite="MBM ¶3-1", tier="likely", origin="direct")
        m.set(REQUEST, "5-4", "reject", cite="MBM ¶5-4", tier="check", origin="ripple")
        m.set("Tire pressure for fixed gear is now 48 psi.", "7-1", "add-missed", cite="MBM ¶7-1")
        exported = m.export()

    other = tmp_path / "other.sqlite"
    other.write_bytes(manual_db.read_bytes())
    with Marks(other) as m:
        m.clear_all()
        assert m.import_jsonl(exported) == 3
        assert m.export() == exported


def test_import_refuses_marks_from_another_manual(store):
    rec = {"request_id": "cr-x", "request": REQUEST, "doc": "Some Other Manual", "loc_id": "3-1",
           "cite": "x", "verdict": "confirm", "tier": None, "origin": None,
           "marked_at": "2026-01-01T00:00:00+00:00"}
    with pytest.raises(ValueError, match="Some Other Manual"):
        store.import_jsonl(json.dumps(rec))
