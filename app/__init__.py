"""The local app (SPEC §5.4): paste a change request, see the tiered spots, mark each one.

    flask --app app run                          # against build/faa.sqlite
    LOCATOR_DB=build/other.sqlite flask --app app run
    flask --app app import-marks marks.jsonl     # load an export back in

One writer on one machine: no login, no multi-user support. Marks are saved in
the corpus SQLite file as they are clicked (app/marks.py) and export as JSONL.

Routes:
  GET  /                  the page; ?request=<text> runs the baseline locator
  POST /marks             save one mark: {request, loc_id, verdict, tier, origin};
                          verdict "clear" removes it
  GET  /marks.jsonl       export every mark, or ?request_id=<id> for one request
"""
from __future__ import annotations

import os
import re
import threading
from pathlib import Path

import click
from flask import Flask, Response, abort, jsonify, render_template, request

from app.marks import VERDICTS, Marks, request_id
from locator import search, tiers

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_DB = ROOT / "build/faa.sqlite"


def create_app(db: Path | str | None = None, embed: search.QueryEmbed | None = None) -> Flask:
    """db: the corpus index (default $LOCATOR_DB, else build/faa.sqlite).
    embed: the query embedder; tests pass a fake. By default the index's own model
    is loaded on the first search (a few seconds) and kept."""
    app = Flask(__name__)
    db_path = Path(db or os.environ.get("LOCATOR_DB") or DEFAULT_DB)
    loaded: list[search.QueryEmbed] = [embed] if embed else []
    lock = threading.Lock()

    def embedder(index: search.Index) -> search.QueryEmbed:
        with lock:
            if not loaded:
                loaded.append(search.query_embedder(index.meta["embed_model"]))
        return loaded[0]

    def run(index: search.Index, text: str) -> tiers.Tiers:
        return tiers.tier(search.locate(index, search.read_request(text), embed=embedder(index)))

    @app.before_request
    def need_index():
        if not db_path.exists():
            return Response(f"No index at {db_path}. Build it with: python scripts/build_index.py "
                            "build/faa_chunks.jsonl", status=500, mimetype="text/plain")

    @app.get("/")
    def page():
        text = request.args.get("request", "").strip()
        with search.Index(db_path) as index, Marks(db_path) as marks:
            scope = index.scope()
            if not text:
                return render_template("index.html", scope=scope, text="")
            rid = request_id(text)
            saved = marks.for_request(rid)
            return render_template(
                "index.html", scope=scope, text=text, rid=rid,
                read=tiers.describe(search.read_request(text)), found=run(index, text),
                marks=saved, missed=[m for m in saved.values() if m["verdict"] == "add-missed"])

    @app.post("/marks")
    def save_mark():
        data = request.get_json(silent=True) or {}
        text = str(data.get("request") or "").strip()
        typed, verdict = str(data.get("loc_id") or ""), data.get("verdict")
        tier, origin = data.get("tier"), data.get("origin")
        if not text or not typed.strip():
            return _error("a mark needs the request and a spot")
        if verdict not in VERDICTS + ("clear",):
            return _error(f"verdict must be one of {', '.join(VERDICTS)} or clear")
        if tier not in (None, "likely", "check") or origin not in (None, "direct", "ripple"):
            return _error("tier must be likely or check; origin direct or ripple")
        rid = request_id(text)
        if verdict == "clear":
            with Marks(db_path) as marks:
                marks.clear(rid, typed)
            return jsonify(request_id=rid, loc_id=typed, verdict=None)

        with search.Index(db_path) as index:
            chunks = find_loc(index, typed)
            if not chunks:
                return _error(f"no spot {typed.strip()!r} in {index.scope()}")
            if len(chunks) > 1:
                return _error(f"{typed.strip()!r} is used more than once; type the one you mean: "
                              + "; ".join(f"{c.loc_id} ({c.cite})" for c in chunks))
            chunk = chunks[0]
            if verdict == "add-missed":
                found = run(index, text)
                if chunk.loc_id in {s.loc_id for s in found.likely + found.check}:
                    return _error(f"{chunk.cite} is already listed; confirm it there instead")
        with Marks(db_path) as marks:
            marks.set(text, chunk.loc_id, verdict, cite=chunk.cite, tier=tier, origin=origin)
        return jsonify(request_id=rid, loc_id=chunk.loc_id, cite=chunk.cite, verdict=verdict)

    @app.get("/marks.jsonl")
    def export_marks():
        rid = request.args.get("request_id") or None
        if rid is not None and not re.fullmatch(r"cr-[0-9a-f]+", rid):
            abort(400)
        with Marks(db_path) as marks:
            body = marks.export(rid)
        name = f"marks-{rid}.jsonl" if rid else "marks.jsonl"
        return Response(body, mimetype="application/x-ndjson",
                        headers={"Content-Disposition": f"attachment; filename={name}"})

    @app.cli.command("import-marks")
    @click.argument("path", type=click.Path(exists=True, dir_okay=False, path_type=Path))
    def import_marks(path: Path) -> None:
        """Load exported marks (JSONL) back into the index file."""
        with Marks(db_path) as marks:
            try:
                n = marks.import_jsonl(path.read_text())
            except ValueError as e:
                raise click.ClickException(str(e))
        click.echo(f"imported {n} marks into {db_path}")

    return app


def _error(message: str):
    return jsonify(error=message), 400


def find_loc(index: search.Index, typed: str) -> list[search.Chunk]:
    """The chunks a writer means by "7-144", "¶7-144", "para 7-144" or "Figure 7-9".

    More than one when the manual uses the number twice: the parser keys the
    later one with its PDF page ("11-48@p509"), and typing that picks it alone.
    """
    s = " ".join(typed.split())
    s = re.sub(r"^(?:¶|para(?:graph)?\.?)\s*", "", s, flags=re.I)
    s = re.sub(r"^(figure|table|appendix)\s+", lambda m: m.group(1).lower() + "-", s, flags=re.I)
    return [c for c in index.chunks.values()
            if c.loc_id == s or ("@" not in s and c.loc_id.startswith(s + "@p"))]
