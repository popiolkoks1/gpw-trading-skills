import shutil
import sys
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parents[2] / "skills" / "gpw-daily-brief" / "scripts"
sys.path.insert(0, str(SCRIPTS))

FIXTURES = Path(__file__).resolve().parent / "fixtures"


@pytest.fixture
def fixtures():
    return FIXTURES


@pytest.fixture
def report_dir(tmp_path):
    """A folder holding the trimmed real reports of 2026-11-12 (previous)
    and 2026-11-13 (current) - the shape `run` produces."""
    d = tmp_path / "reports"
    d.mkdir()
    for sub in ("prev", "cur"):
        for p in (FIXTURES / sub).glob("*.json"):
            shutil.copy(p, d / p.name)
    return d
