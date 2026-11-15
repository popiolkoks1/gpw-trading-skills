#!/usr/bin/env python3
"""Build uploadable ``.skill`` (zip) archives for the Claude web app.

    python3 scripts/package_skills.py            # all skills -> dist/
    python3 scripts/package_skills.py --skill gpw-market-breadth

Each archive contains one top-level folder named after the skill, with
SKILL.md, references/, scripts/ (incl. the vendored gpwlib) and assets/.
Tests, evals, caches and bytecode are excluded. A SHA256SUMS file is written
next to the archives.
"""

from __future__ import annotations

import argparse
import hashlib
import re
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
EXCLUDE_DIRS = {"__pycache__", "tests", "evals", ".pytest_cache"}
EXCLUDE_SUFFIX = {".pyc", ".tmp"}
NAME_RE = re.compile(r"^[a-z0-9-]{1,64}$")


def validate(skill_dir: Path) -> None:
    text = (skill_dir / "SKILL.md").read_text(encoding="utf-8")
    m = re.match(r"^---\n(.*?)\n---\n", text, re.S)
    if not m:
        raise SystemExit("{}: SKILL.md has no YAML frontmatter".format(skill_dir.name))
    fm = m.group(1)
    name = re.search(r"^name:\s*(.+)$", fm, re.M)
    desc = re.search(r"^description:\s*(.+)$", fm, re.M)
    if not name or name.group(1).strip() != skill_dir.name or not NAME_RE.match(skill_dir.name):
        raise SystemExit("{}: frontmatter name must equal folder name (lowercase, digits, hyphens)".format(skill_dir.name))
    if not desc or not (0 < len(desc.group(1)) <= 1024):
        raise SystemExit("{}: description missing or longer than 1024 chars".format(skill_dir.name))
    if "<" in desc.group(1) or ">" in desc.group(1):
        raise SystemExit("{}: description must not contain XML/angle brackets".format(skill_dir.name))
    lines = text.count("\n")
    if lines > 500:
        raise SystemExit("{}: SKILL.md has {} lines (limit 500)".format(skill_dir.name, lines))


def build(skill_dir: Path, dist: Path) -> Path:
    validate(skill_dir)
    out = dist / "{}.skill".format(skill_dir.name)
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as zf:
        for p in sorted(skill_dir.rglob("*")):
            rel = p.relative_to(skill_dir)
            if any(part in EXCLUDE_DIRS for part in rel.parts) or p.suffix in EXCLUDE_SUFFIX or p.is_dir():
                continue
            zf.write(p, Path(skill_dir.name) / rel)
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--skill")
    ap.add_argument("--dist", default=str(ROOT / "dist"))
    a = ap.parse_args()
    dist = Path(a.dist)
    dist.mkdir(parents=True, exist_ok=True)
    skills = [ROOT / "skills" / a.skill] if a.skill else sorted(p for p in (ROOT / "skills").iterdir() if (p / "SKILL.md").exists())
    sums = []
    for s in skills:
        out = build(s, dist)
        digest = hashlib.sha256(out.read_bytes()).hexdigest()
        sums.append("{}  {}".format(digest, out.name))
        print("built", out.relative_to(ROOT) if out.is_relative_to(ROOT) else out)
    (dist / "SHA256SUMS").write_text("\n".join(sums) + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
