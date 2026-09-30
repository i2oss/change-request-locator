"""Parser contract gate (SPEC §4.2). Every parser's JSONL must pass this.

    python scripts/check_chunks.py build/faa_chunks.jsonl [--report build/unresolved_refs.md]

Checks:
  1. every chunk validates against schema/chunk.schema.json
  2. every `id` is unique (and every loc.id is unique within its doc)
  3. every `refs_out` entry resolves to a loc.id in the same doc, or is listed in
     the unresolved report; nothing is dropped silently
  4. every chunk has a `type`

Exit code 1 if 1, 2 or 4 fails. Unresolved refs don't fail the gate; they're
written to the report for a person to review.
"""
from __future__ import annotations

import argparse
import collections
import json
import sys
from pathlib import Path

from jsonschema import Draft202012Validator

ROOT = Path(__file__).resolve().parent.parent
SCHEMA = ROOT / "schema/chunk.schema.json"


def load(path: Path) -> list[dict]:
    chunks = []
    with path.open() as f:
        for n, line in enumerate(f, 1):
            if line.strip():
                try:
                    chunks.append(json.loads(line))
                except json.JSONDecodeError as e:
                    sys.exit(f"{path}:{n}: not valid JSON: {e}")
    return chunks


def check(chunks: list[dict]) -> tuple[list[str], list[dict]]:
    """Returns (errors, unresolved refs)."""
    validator = Draft202012Validator(json.loads(SCHEMA.read_text()))
    errors: list[str] = []
    for c in chunks:
        for e in validator.iter_errors(c):
            where = "/".join(str(p) for p in e.absolute_path) or "(record)"
            errors.append(f"schema: {c.get('id', '?')}: {where}: {e.message}")
        if not c.get("type"):
            errors.append(f"type: {c.get('id', '?')}: missing type")

    for key, what in ((lambda c: c.get("id"), "id"),
                      (lambda c: (c.get("doc"), c.get("loc", {}).get("id")), "loc.id")):
        counts = collections.Counter(key(c) for c in chunks)
        errors += [f"duplicate {what}: {k}" for k, n in counts.items() if n > 1]

    locs = collections.defaultdict(set)
    for c in chunks:
        locs[c.get("doc")].add(c.get("loc", {}).get("id"))
    unresolved = [
        {"chunk": c["id"], "loc": c["loc"]["id"], "ref": r}
        for c in chunks for r in c.get("refs_out", [])
        if r not in locs[c.get("doc")]
    ]
    return errors, unresolved


def write_report(path: Path, chunks: list[dict], unresolved: list[dict]) -> None:
    by_ref = collections.defaultdict(list)
    for u in unresolved:
        by_ref[u["ref"]].append(u["loc"])
    total = sum(len(c.get("refs_out", [])) for c in chunks)
    lines = [
        "# Unresolved refs",
        "",
        f"{len(unresolved)} of {total} `refs_out` entries point at a loc.id that no chunk has "
        f"({len(by_ref)} distinct targets).",
        "",
        "| Target | Referenced from |",
        "|---|---|",
    ]
    for ref in sorted(by_ref, key=_sort_key):
        lines.append(f"| `{ref}` | {', '.join(sorted(set(by_ref[ref]), key=_sort_key))} |")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n")


def _sort_key(s: str):
    import re
    return [int(t) if t.isdigit() else t for t in re.split(r"(\d+)", s)]


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("jsonl", type=Path)
    ap.add_argument("--report", type=Path, default=ROOT / "build/unresolved_refs.md")
    args = ap.parse_args()

    chunks = load(args.jsonl)
    errors, unresolved = check(chunks)
    write_report(args.report, chunks, unresolved)

    types = collections.Counter(c.get("type") for c in chunks)
    print(f"{len(chunks)} chunks: " + ", ".join(f"{t} {n}" for t, n in types.most_common()))
    print(f"unresolved refs: {len(unresolved)} -> {args.report}")
    if errors:
        print(f"FAIL: {len(errors)} contract errors")
        for e in errors[:50]:
            print("  " + e)
        sys.exit(1)
    print("PASS: schema, unique ids, types; every unresolved ref is in the report")


if __name__ == "__main__":
    main()
