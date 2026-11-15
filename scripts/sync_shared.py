#!/usr/bin/env python3
"""Vendor shared/gpwlib into every skill that declares it needs it.

A skill opts in by having a ``scripts/`` folder and listing ``gpwlib`` in
``skills/<skill>/scripts/.vendor`` (one module name per line).

    python3 scripts/sync_shared.py          # copy
    python3 scripts/sync_shared.py --check  # exit 1 if any copy drifted (CI)
"""

from __future__ import annotations

import argparse
import filecmp
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SHARED = ROOT / "shared" / "gpwlib"


def targets():
    for marker in sorted((ROOT / "skills").glob("*/scripts/.vendor")):
        if "gpwlib" in marker.read_text().split():
            yield marker.parent / "gpwlib"


def py_files(folder: Path):
    return sorted(p.name for p in folder.glob("*.py"))


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", action="store_true")
    args = ap.parse_args()
    drift = []
    for dest in targets():
        if args.check:
            if not dest.exists() or py_files(dest) != py_files(SHARED):
                drift.append(str(dest))
                continue
            for name in py_files(SHARED):
                if not filecmp.cmp(SHARED / name, dest / name, shallow=False):
                    drift.append(str(dest / name))
        else:
            if dest.exists():
                shutil.rmtree(dest)
            shutil.copytree(SHARED, dest, ignore=shutil.ignore_patterns("__pycache__"))
            print("synced", dest.relative_to(ROOT))
    if drift:
        print("Vendored gpwlib out of date:\n  " + "\n  ".join(drift), file=sys.stderr)
        print("Run: python3 scripts/sync_shared.py", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
