"""Locate the spots a change request touches, with no LLM (the baseline).

    python -m locator "<request text>" --db build/faa.sqlite

Prints what was searched, then the Likely and Check these spots, each with its
citation, a one-line reason and a snippet («» marks what was found).
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from locator import search, tiers


def describe(request: search.Request) -> str:
    """What the locator read from the request, so the writer can see what drove it."""
    parts = [("specs", request.specs), ("refs", [tiers.show_loc(r) for r in request.refs]),
             ("values", request.values), ("words", request.terms)]
    return " · ".join(f"{name} {', '.join(items) or '—'}" for name, items in parts)


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(prog="python -m locator", description=__doc__.split("\n")[0])
    ap.add_argument("request", help="the change request text")
    ap.add_argument("--db", type=Path, default=Path("build/faa.sqlite"))
    args = ap.parse_args(argv)
    if not args.db.exists():
        sys.exit(f"no index at {args.db}; build it with scripts/build_index.py")

    request = search.read_request(args.request)
    with search.Index(args.db) as index:
        embed = search.query_embedder(index.meta["embed_model"])
        found = search.locate(index, request, embed=embed)
        print(tiers.render(index.scope(), describe(request), tiers.tier(found)), end="")


if __name__ == "__main__":
    main()
