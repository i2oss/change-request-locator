"""Chunk JSONL -> one SQLite file per corpus (SPEC §5.1).

    python scripts/build_index.py build/faa_chunks.jsonl [-o build/faa.sqlite]

The file holds everything the locator searches, so it copies to another machine
and opens with plain `sqlite3` (FTS5 is built into standard SQLite):

  chunks            every chunk field; lists and `extra` stored as JSON text
  chunk_refs        (chunk_id, ref)     one row per refs_out entry
  chunk_specs       (chunk_id, spec)    one row per specs entry
  chunk_applies_to  (chunk_id, variant) one row per applies_to entry
  chunks_fts        FTS5 keyword index over text + heading_path; its rowid is chunks.seq
  embeddings        chunk_id -> float32 vector, normalised to length 1 so cosine = dot
  meta              doc, chunk_count, embed_model, embed_dim, source, built_at

The local app adds the writer's marks (app/marks.py). A rebuild copies them
into the new file, so they are never lost.

The JSONL must pass the parser contract (scripts/check_chunks.py) first.
"""
from __future__ import annotations

import argparse
import datetime
import json
import sqlite3
import sys
import time
from pathlib import Path
from typing import Callable

import numpy as np

ROOT = Path(__file__).resolve().parent.parent
sys.path[:0] = [str(ROOT), str(ROOT / "scripts")]

import check_chunks  # noqa: E402
from locator import config  # noqa: E402

Embed = Callable[[list[str]], np.ndarray]

# Tables the index build doesn't make but must keep on a rebuild (app/marks.py).
KEEP_TABLES = ("change_requests", "marks")

SCHEMA = """
CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);

CREATE TABLE chunks (
    seq          INTEGER PRIMARY KEY,   -- order in the JSONL; also the FTS rowid
    id           TEXT NOT NULL UNIQUE,
    doc          TEXT NOT NULL,
    loc_scheme   TEXT NOT NULL,
    loc_id       TEXT NOT NULL,
    loc_cite     TEXT NOT NULL,
    heading_path TEXT NOT NULL,         -- JSON list
    type         TEXT NOT NULL,
    text         TEXT NOT NULL,
    refs_out     TEXT NOT NULL,         -- JSON list
    specs        TEXT NOT NULL,         -- JSON list
    applies_to   TEXT NOT NULL,         -- JSON list; [] = all variants
    page         TEXT,
    extra        TEXT NOT NULL,         -- JSON object; never read by the pipeline
    UNIQUE (doc, loc_id)
);

CREATE TABLE chunk_refs (
    chunk_id TEXT NOT NULL REFERENCES chunks(id),
    ref      TEXT NOT NULL,
    PRIMARY KEY (chunk_id, ref)
);
CREATE INDEX chunk_refs_ref ON chunk_refs(ref);

CREATE TABLE chunk_specs (
    chunk_id TEXT NOT NULL REFERENCES chunks(id),
    spec     TEXT NOT NULL,
    PRIMARY KEY (chunk_id, spec)
);
CREATE INDEX chunk_specs_spec ON chunk_specs(spec);

CREATE TABLE chunk_applies_to (
    chunk_id TEXT NOT NULL REFERENCES chunks(id),
    variant  TEXT NOT NULL,
    PRIMARY KEY (chunk_id, variant)
);
CREATE INDEX chunk_applies_to_variant ON chunk_applies_to(variant);

CREATE VIRTUAL TABLE chunks_fts USING fts5(text, heading_path, tokenize = 'porter unicode61');

CREATE TABLE embeddings (
    chunk_id TEXT PRIMARY KEY REFERENCES chunks(id),
    vector   BLOB NOT NULL              -- float32, little-endian, embed_dim values
);
"""


def embed_text(c: dict) -> str:
    """What gets embedded: the heading breadcrumb, then the chunk text."""
    return " > ".join(c["heading_path"]) + "\n" + c["text"]


def load_embedder(model: str) -> Embed:
    """sentence-transformers on CPU. Imported here so tests with a fake embedder skip torch."""
    from sentence_transformers import SentenceTransformer

    st = SentenceTransformer(model, device="cpu")
    return lambda texts: st.encode(texts, batch_size=32, show_progress_bar=True,
                                   convert_to_numpy=True)


def build(chunks: list[dict], out: Path, embed: Embed, model: str, source: str = "") -> dict:
    """Writes the index to `out`, replacing any old file. Returns the meta values."""
    errors, _ = check_chunks.check(chunks)
    if errors:
        raise ValueError(f"{len(errors)} contract errors, e.g. " + "; ".join(errors[:3]))
    docs = {c["doc"] for c in chunks}
    if len(docs) != 1:
        raise ValueError(f"an index holds one doc; got {len(docs)}: {sorted(docs)}")
    kept = _kept_tables(out, next(iter(docs)))

    vectors = np.asarray(embed([embed_text(c) for c in chunks]), dtype=np.float32)
    if vectors.shape[0] != len(chunks):
        raise ValueError(f"embedder returned {vectors.shape[0]} vectors for {len(chunks)} chunks")
    vectors /= np.linalg.norm(vectors, axis=1, keepdims=True)

    meta = {
        "doc": docs.pop(),
        "chunk_count": str(len(chunks)),
        "embed_model": model,
        "embed_dim": str(vectors.shape[1]),
        "source": source,
        "built_at": datetime.datetime.now(datetime.UTC).isoformat(timespec="seconds"),
    }

    # Build beside the target and swap in at the end, so a failed build never
    # leaves a half-written index where the old one was.
    tmp = out.with_name(out.name + ".tmp")
    tmp.unlink(missing_ok=True)
    out.parent.mkdir(parents=True, exist_ok=True)
    try:
        conn = sqlite3.connect(tmp)
        with conn:
            conn.executescript(SCHEMA)
            _insert(conn, chunks, vectors, meta)
        if kept:
            conn.execute("ATTACH DATABASE ? AS old", (str(out),))
            with conn:
                for name, sql in kept:
                    conn.execute(sql)
                    conn.execute(f"INSERT INTO main.{name} SELECT * FROM old.{name}")
            conn.execute("DETACH DATABASE old")
        conn.close()
        tmp.replace(out)
    finally:
        tmp.unlink(missing_ok=True)
    return meta


def _kept_tables(out: Path, doc: str) -> list[tuple[str, str]]:
    """(name, CREATE sql) of the KEEP_TABLES in the old index, if there is one.
    Refuses to replace an index of another doc that holds marks."""
    if not out.exists():
        return []
    conn = sqlite3.connect(out.resolve().as_uri() + "?mode=ro", uri=True)
    try:
        marks = ",".join("?" * len(KEEP_TABLES))
        kept = conn.execute(f"SELECT name, sql FROM sqlite_master WHERE type = 'table'"
                            f" AND name IN ({marks}) ORDER BY rootpage", KEEP_TABLES).fetchall()
        if not kept:
            return []
        old_doc = conn.execute("SELECT value FROM meta WHERE key = 'doc'").fetchone()[0]
        held = sum(conn.execute(f"SELECT count(*) FROM {name}").fetchone()[0] for name, _ in kept)
    finally:
        conn.close()
    if old_doc != doc and held:
        raise ValueError(f"{out} holds marks for {old_doc!r}; export them "
                         f"(the app's Export button) and move the file before building {doc!r}")
    return kept if old_doc == doc else []


def _insert(conn: sqlite3.Connection, chunks: list[dict], vectors: np.ndarray, meta: dict) -> None:
    conn.executemany("INSERT INTO meta VALUES (?, ?)", meta.items())
    for seq, (c, v) in enumerate(zip(chunks, vectors), 1):
        specs, applies_to = c.get("specs", []), c.get("applies_to", [])
        conn.execute(
            "INSERT INTO chunks VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (seq, c["id"], c["doc"], c["loc"]["scheme"], c["loc"]["id"], c["loc"]["cite"],
             json.dumps(c["heading_path"]), c["type"], c["text"], json.dumps(c["refs_out"]),
             json.dumps(specs), json.dumps(applies_to), c.get("page"),
             json.dumps(c.get("extra", {}))))
        conn.executemany("INSERT INTO chunk_refs VALUES (?, ?)", [(c["id"], r) for r in c["refs_out"]])
        conn.executemany("INSERT INTO chunk_specs VALUES (?, ?)", [(c["id"], s) for s in specs])
        # The schema doesn't require unique applies_to entries; look each up once.
        conn.executemany("INSERT INTO chunk_applies_to VALUES (?, ?)",
                         [(c["id"], a) for a in dict.fromkeys(applies_to)])
        conn.execute("INSERT INTO chunks_fts (rowid, text, heading_path) VALUES (?, ?, ?)",
                     (seq, c["text"], " > ".join(c["heading_path"])))
        conn.execute("INSERT INTO embeddings VALUES (?, ?)", (c["id"], v.astype("<f4").tobytes()))


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("jsonl", type=Path)
    ap.add_argument("-o", "--out", type=Path,
                    help="default: build/<corpus>.sqlite, e.g. faa_chunks.jsonl -> build/faa.sqlite")
    args = ap.parse_args()
    out = args.out or ROOT / "build" / (args.jsonl.stem.removesuffix("_chunks") + ".sqlite")

    start = time.monotonic()
    chunks = check_chunks.load(args.jsonl)
    try:
        meta = build(chunks, out, embed=load_embedder(config.EMBED_MODEL),
                     model=config.EMBED_MODEL, source=args.jsonl.name)
    except ValueError as e:
        sys.exit(f"FAIL: {e}")
    size = out.stat().st_size / 1e6
    print(f"{out}: {meta['doc']}, {meta['chunk_count']} chunks, {meta['embed_model']} "
          f"({meta['embed_dim']} dims), {size:.1f} MB in {time.monotonic() - start:.0f}s")


if __name__ == "__main__":
    main()
