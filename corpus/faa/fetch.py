"""Download the FAA source PDFs (SPEC §3.1). They are gitignored, never committed.

    python corpus/faa/fetch.py          # the indexed manual (w-chg1)
    python corpus/faa/fetch.py --all    # also the 2024 Editorial Update (eval step 3)

Each file's SHA-256 is pinned so a silently re-published PDF is caught before
it changes the chunks. `sha256=None` means "not pinned yet": the hash is
printed so it can be pasted in.
"""
import argparse
import hashlib
import sys
import urllib.request
from pathlib import Path

HERE = Path(__file__).parent
BASE = "https://www.faa.gov/documentLibrary/media/Advisory_Circular/"

FILES = {
    "AC_43.13-1B_w-chg1.pdf": "8dc99dd41334381b287ae02d03dc29928d9eacb1035b67aa24c5a0525324ee73",
    "AC_43.13-1B_CHG_1_Ed_Upd_FAA.pdf": None,
}
DEFAULT = "AC_43.13-1B_w-chg1.pdf"


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def fetch(name: str) -> Path:
    dest = HERE / name
    if not dest.exists():
        print(f"downloading {name} ...")
        # faa.gov rejects urllib's default user agent.
        req = urllib.request.Request(BASE + name, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req) as resp, dest.open("wb") as out:
            while block := resp.read(1 << 20):
                out.write(block)
    got, want = sha256(dest), FILES[name]
    if want is None:
        print(f"{name}: sha256 {got} (not pinned)")
    elif got != want:
        sys.exit(f"{name}: sha256 mismatch\n  expected {want}\n  got      {got}\n"
                 "FAA may have re-published the file; re-check the parser before re-pinning.")
    else:
        print(f"{name}: ok")
    return dest


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--all", action="store_true", help="also fetch the 2024 Editorial Update")
    args = ap.parse_args()
    for name in FILES if args.all else [DEFAULT]:
        fetch(name)
