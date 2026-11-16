import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
SCRIPTS = HERE.parents[1] / "skills" / "gpw-pre-trade-gate" / "scripts"
sys.path.insert(0, str(SCRIPTS))
# shared synthetic GPW cache builder
sys.path.insert(0, str(HERE.parent / "gpw-portfolio-check"))
