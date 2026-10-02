"""The writer's marks on spots, saved in the corpus SQLite file (SPEC §2, §5.2 step 5).

    with Marks("build/faa.sqlite") as marks:
        rid = marks.set(request_text, "7-144", "confirm", cite=..., tier="likely", origin="direct")
        marks.for_request(rid)        # {loc_id: mark}
        marks.export()                # JSONL, one mark per line

Two tables sit beside the index tables:
  change_requests  (id, text)       id is a hash of the text, so pasting the
                                    same request again finds its marks
  marks            one verdict per (change request, loc.id): confirm, reject
                   or add-missed. tier/origin record what the writer saw;
                   add-missed spots have neither.

scripts/build_index.py copies both tables into a rebuilt index, so a rebuild
never loses marks.
"""
from __future__ import annotations

import datetime
import hashlib
import json
import sqlite3
from pathlib import Path

VERDICTS = ("confirm", "reject", "add-missed")
FIELDS = ("request_id", "request", "doc", "loc_id", "cite", "verdict", "tier", "origin",
          "marked_at")

SCHEMA = """
CREATE TABLE IF NOT EXISTS change_requests (
    id   TEXT PRIMARY KEY,               -- "cr-" + hash of the text
    text TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS marks (
    request_id TEXT NOT NULL REFERENCES change_requests(id),
    loc_id     TEXT NOT NULL,
    verdict    TEXT NOT NULL CHECK (verdict IN ('confirm', 'reject', 'add-missed')),
    cite       TEXT NOT NULL,
    tier       TEXT,                     -- likely / check as shown; NULL for add-missed
    origin     TEXT,                     -- direct / ripple as shown; NULL for add-missed
    marked_at  TEXT NOT NULL,            -- UTC, ISO 8601
    PRIMARY KEY (request_id, loc_id)
);
"""


def normalize(text: str) -> str:
    return " ".join(text.split())


def request_id(text: str) -> str:
    """Same words, same id, however the request was spaced or wrapped when pasted."""
    return "cr-" + hashlib.sha256(normalize(text).encode()).hexdigest()[:10]


def _now() -> str:
    return datetime.datetime.now(datetime.UTC).isoformat(timespec="seconds")


class Marks:
    """Read-write access to the marks in one corpus file. Creates the tables if needed."""

    def __init__(self, path: Path | str):
        self.conn = sqlite3.connect(path)
        self.conn.row_factory = sqlite3.Row
        with self.conn:
            self.conn.executescript(SCHEMA)
        self.doc = self.conn.execute("SELECT value FROM meta WHERE key = 'doc'").fetchone()[0]

    def __enter__(self) -> Marks:
        return self

    def __exit__(self, *exc) -> None:
        self.conn.close()

    def set(self, request: str, loc_id: str, verdict: str, cite: str,
            tier: str | None = None, origin: str | None = None,
            marked_at: str | None = None) -> str:
        """Save one verdict, replacing any earlier one on that spot. Returns the request id."""
        if verdict not in VERDICTS:
            raise ValueError(f"verdict must be one of {', '.join(VERDICTS)}; got {verdict!r}")
        if verdict == "add-missed":
            tier = origin = None
        rid = request_id(request)
        with self.conn:
            self.conn.execute("INSERT OR IGNORE INTO change_requests VALUES (?, ?)",
                              (rid, normalize(request)))
            self.conn.execute("INSERT OR REPLACE INTO marks VALUES (?, ?, ?, ?, ?, ?, ?)",
                              (rid, loc_id, verdict, cite, tier, origin, marked_at or _now()))
        return rid

    def clear(self, rid: str, loc_id: str) -> None:
        with self.conn:
            self.conn.execute("DELETE FROM marks WHERE request_id = ? AND loc_id = ?", (rid, loc_id))

    def clear_all(self) -> None:
        with self.conn:
            self.conn.execute("DELETE FROM marks")
            self.conn.execute("DELETE FROM change_requests")

    def for_request(self, rid: str) -> dict[str, dict]:
        """{loc_id: mark} for one change request, in the order they were marked."""
        return {r["loc_id"]: dict(r) for r in self._rows(rid)}

    def _rows(self, rid: str | None = None) -> list[sqlite3.Row]:
        sql = ("SELECT m.request_id, r.text AS request, m.loc_id, m.cite, m.verdict, m.tier,"
               " m.origin, m.marked_at FROM marks m JOIN change_requests r ON r.id = m.request_id")
        args: tuple = ()
        if rid is not None:
            sql, args = sql + " WHERE m.request_id = ?", (rid,)
        return self.conn.execute(sql + " ORDER BY m.request_id, m.marked_at, m.rowid", args).fetchall()

    def export(self, rid: str | None = None) -> str:
        """Marks as JSONL, one per line; every request when rid is None."""
        return "".join(json.dumps({k: (self.doc if k == "doc" else r[k]) for k in FIELDS},
                                  ensure_ascii=False) + "\n"
                       for r in self._rows(rid))

    def import_jsonl(self, text: str) -> int:
        """Load exported marks back in, replacing marks on the same spots. Returns the count."""
        records = [json.loads(line) for line in text.splitlines() if line.strip()]
        for rec in records:
            if rec["doc"] != self.doc:
                raise ValueError(f"mark for {rec['doc']!r} can't go in the index of {self.doc!r}")
        for rec in records:
            self.set(rec["request"], rec["loc_id"], rec["verdict"], rec["cite"],
                     rec["tier"], rec["origin"], rec["marked_at"])
        return len(records)
