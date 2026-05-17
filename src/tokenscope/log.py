"""Single-source logging setup for tokenscope.

Why a dedicated module:
  Streamlit re-executes the app script on every user interaction. Calling
  `logging.basicConfig` from `app.py` (or scattering it across modules)
  appends a duplicate stderr handler on every rerun, so by the tenth
  click each log line prints ten times. This module's `setup_logging()`
  is idempotent — it tags the handler it installs and refuses to add a
  second one.

  Centralising also means there is exactly one place that resolves the
  log level, one format string, one handler target. The persona's "one
  authoritative path" principle.

Level resolution order:
  1. Explicit `level` argument to `setup_logging`.
  2. `TOKENSCOPE_LOG_LEVEL` environment variable.
  3. Default: ``WARNING`` (quiet on the happy path; flip to ``DEBUG`` for
     diagnosis).

Format:
  ``%(asctime)s %(levelname)-7s %(name)s | %(message)s`` on stderr.
  ISO-style date. Per-module logger names via ``logging.getLogger(__name__)``
  so every line carries its origin.

Docker:
  stderr is captured by ``docker logs <container>``. No rotation,
  no remote shipping — a local dashboard is the audience.
"""

from __future__ import annotations

import logging
import os
import sys

_HANDLER_MARKER = "_tokenscope_handler"
_DEFAULT_LEVEL = "WARNING"
_LEVEL_ENV_VAR = "TOKENSCOPE_LOG_LEVEL"

_FORMAT = "%(asctime)s %(levelname)-7s %(name)s | %(message)s"
_DATEFMT = "%Y-%m-%d %H:%M:%S"


def setup_logging(level: str | None = None) -> None:
    """Configure the ``tokenscope`` logger tree with one stderr handler.

    Idempotent: re-running this on a subsequent Streamlit rerun is a
    no-op except for the level update (we still honour a changed env
    var or explicit arg without duplicating handlers).
    """
    resolved_level = _resolve_level(level)

    root = logging.getLogger("tokenscope")
    root.setLevel(resolved_level)
    # Don't bubble to the Python root — Streamlit installs its own root
    # handler that prints WARNING+ to its server stdout. We don't want
    # every tokenscope log line printed twice (once via our stderr,
    # once via Streamlit's).
    root.propagate = False

    if not any(getattr(h, _HANDLER_MARKER, False) for h in root.handlers):
        handler = logging.StreamHandler(sys.stderr)
        handler.setFormatter(logging.Formatter(_FORMAT, datefmt=_DATEFMT))
        setattr(handler, _HANDLER_MARKER, True)
        root.addHandler(handler)


def get_logger(name: str) -> logging.Logger:
    """Thin wrapper over `logging.getLogger`. Exists so callers import
    from `tokenscope.log` (single source of truth) rather than the
    stdlib — keeps the dependency edge explicit and lets us swap
    implementations later without ripping every import."""
    return logging.getLogger(name)


def _resolve_level(level: str | None) -> int:
    """Explicit arg → env var → default. Returns the numeric level."""
    raw = level or os.environ.get(_LEVEL_ENV_VAR, "").strip() or _DEFAULT_LEVEL
    numeric = logging.getLevelName(raw.upper())
    if isinstance(numeric, int):
        return numeric
    # `getLevelName` returns the string "Level X" for unknown names —
    # treat that as junk and fall back to the default.
    return logging.WARNING
