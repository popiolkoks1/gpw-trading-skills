"""gpwlib - shared, stdlib-only helpers for the GPW (Warsaw Stock Exchange) skills.

This package is the canonical source. Each skill ships a vendored copy under
``skills/<skill>/scripts/gpwlib/`` so that every skill can be installed or
uploaded on its own. Run ``python3 scripts/sync_shared.py`` after editing and
``python3 scripts/sync_shared.py --check`` in CI to detect drift.
"""

__version__ = "1.1.0"
