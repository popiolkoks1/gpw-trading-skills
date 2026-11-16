import sys
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parents[2] / "skills" / "gpw-account-planner" / "scripts"
sys.path.insert(0, str(SCRIPTS))
