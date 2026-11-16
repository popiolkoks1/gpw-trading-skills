import sys
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parents[2] / "skills" / "gpw-risk-lab" / "scripts"
sys.path.insert(0, str(SCRIPTS))
