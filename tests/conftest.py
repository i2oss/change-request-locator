"""A small made-up manual, indexed with a fake embedder, shared by the locator tests.

Every chunk, spec number and value here is invented. None of it comes from the
2024 FAA edits (SPEC §6.4).
"""
import re
import sys
import zlib
from pathlib import Path

import numpy as np
import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path[:0] = [str(ROOT), str(ROOT / "scripts")]

import build_index  # noqa: E402

DOC = "Made-up Brake Manual rev A"
DIM = 64


def chunk(loc_id, text, **kw):
    c = {
        "id": f"m:{loc_id}",
        "doc": DOC,
        "loc": {"scheme": "faa-para", "id": loc_id, "cite": f"MBM ¶{loc_id}"},
        "heading_path": kw.pop("heading_path", ["CHAPTER 3. BRAKES"]),
        "type": kw.pop("type", "text"),
        "text": text,
        "refs_out": kw.pop("refs_out", []),
    }
    c.update(kw)
    return c


MANUAL = [
    chunk("3-1", "3-1. BRAKE LINING. Linings conform to MIL-B-99001. Replace linings worn "
          "below 0.10 inch.", specs=["MIL-B-99001"], refs_out=["table-3-1"]),
    chunk("table-3-1", "TABLE 3-1. Lining wear limits. MIL-B-99001 lining, minimum 0.10 inch.",
          type="table", specs=["MIL-B-99001"]),
    chunk("3-2", "3-2. BRAKE DISC. Inspect the disc for warping. Discs conform to MIL-D-99007A. "
          "See paragraph 3-1 for linings.", specs=["MIL-D-99007A"], refs_out=["3-1"]),
    chunk("3-2#warning-1", "WARNING: Release hydraulic pressure before you remove the brake.",
          type="warning"),
    chunk("3-3", "3-3. BRAKE BLEEDING. Bleed the brake lines after a disc change.",
          heading_path=["CHAPTER 3. BRAKES", "SECTION 2. BLEEDING"]),
    chunk("3-3f", "f. Bleed until no bubbles show in the fluid.",
          heading_path=["CHAPTER 3. BRAKES", "SECTION 2. BLEEDING"]),
    chunk("5-4", "5-4. WHEEL REMOVAL. Jack the aircraft and remove the wheel. Use a disc to "
          "MIL-D-99007 only.", specs=["MIL-D-99007"], heading_path=["CHAPTER 5. WHEELS"]),
    chunk("5-4#warning-1", "WARNING: Release the hydraulic pressure before you remove the "
          "brake assembly.", type="warning", heading_path=["CHAPTER 5. WHEELS"]),
    chunk("6-1", "6-1. TIRE PRESSURE. Inflate fixed gear tires to 45 psi.",
          applies_to=["Fixed"], heading_path=["CHAPTER 6. TIRES", "6-1. TIRE PRESSURE"]),
    chunk("6-1-r", "6-1. TIRE PRESSURE. Inflate retractable gear tires to 55 psi.",
          applies_to=["Retractable"], heading_path=["CHAPTER 6. TIRES", "6-1. TIRE PRESSURE"]),
    chunk("7-1", "7-1. CLEANING. Wash the landing gear with mild soap and water.",
          heading_path=["CHAPTER 7. CLEANING"]),
    chunk("figure-3-1", "FIGURE 3-1. Brake assembly callouts. Item 4 is part PN-4471-2.",
          type="figure"),
]


def fake_embed(texts):
    """Hashed bag of words: texts sharing words point the same way. Stands in for bge."""
    out = np.zeros((len(texts), DIM), dtype=np.float32)
    for i, t in enumerate(texts):
        for w in re.findall(r"[a-z]{3,}", t.lower()):
            out[i, zlib.crc32(w.encode()) % DIM] += 1
        out[i, 0] += 0.01                     # never all zeros
    return out


@pytest.fixture
def manual_db(tmp_path):
    out = tmp_path / "manual.sqlite"
    build_index.build(MANUAL, out, embed=fake_embed, model="fake-model")
    return out
