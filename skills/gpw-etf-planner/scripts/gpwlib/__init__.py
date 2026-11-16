"""gpwlib - shared, stdlib-only helpers for the GPW (Warsaw Stock Exchange) skills.

This package is the canonical source. Each skill ships a vendored copy under
``skills/<skill>/scripts/gpwlib/`` so that every skill can be installed or
uploaded on its own. Run ``python3 scripts/sync_shared.py`` after editing and
``python3 scripts/sync_shared.py --check`` in CI to detect drift.
"""

__version__ = "1.2.0"


def _utf8_stdio() -> None:
    """Make stdout/stderr UTF-8 when the platform default is not (Windows cp1252
    pipes, as used when Claude runs a script). Reports are in Polish, so the
    default codec would raise UnicodeEncodeError on characters like 'ł'.
    Streams that are already UTF-8, or cannot be reconfigured (pytest capture),
    are left alone."""
    import sys
    for stream in (sys.stdout, sys.stderr):
        enc = (getattr(stream, "encoding", None) or "").lower().replace("_", "-")
        if enc in ("utf-8", "utf8") or not hasattr(stream, "reconfigure"):
            continue
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (ValueError, OSError, AttributeError):
            pass


_utf8_stdio()
