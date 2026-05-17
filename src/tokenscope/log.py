"""Single-source logging setup for tokenscope.

Why a dedicated module:
  Streamlit re-executes the app script on every user interaction. Calling
  `logging.basicConfig` from `app.py` (or scattering it across modules)
  appends a duplicate handler on every rerun, so by the tenth click each
  log line prints ten times. This module's `setup_logging()` is
  idempotent — it tags the handler it installs and refuses to add a
  second one.

  Centralising also means there is exactly one place that resolves the
  log level, one format string, one handler target. The persona's "one
  authoritative path" principle.

Defaults (intentional for a local-first dashboard):
  * Level — ``INFO``. The dashboard is one user on their own laptop;
    verbose-by-default is what shortcuts the "user reports problem →
    we ask them to flip an env var → wait → diagnose" loop that
    repeatedly bit us during UI refactors. ``ERROR`` quietens it down;
    ``DEBUG`` adds the verbose detail.
  * Stream — ``stdout``. ``docker logs <container>`` and a developer's
    terminal both surface stdout; routing logs there means the user
    sees them without additional flags.
  * Format — auto-detected:
      - TTY attached (``sys.stdout.isatty()`` is true) → human-readable
        single-line records with the same shape every other Python tool
        uses. Easy to scan in a terminal.
      - No TTY (containerised, redirected to a file, etc.) → JSON, one
        record per line. ``docker logs ... | jq '.'`` and ``grep`` both
        work out of the box.
    Override via ``TOKENSCOPE_LOG_FORMAT=json|human``.

Level resolution order:
  1. Explicit `level` argument to `setup_logging`.
  2. ``TOKENSCOPE_LOG_LEVEL`` environment variable.
  3. Default: ``INFO``.

Third-party noise suppression:
  Streamlit, Watchdog, urllib3 each log their own internal lifecycle at
  INFO/WARNING. None of it is actionable from a tokenscope user's
  perspective — and on the Live view's 30s refresh cadence the
  websocket-reconnect chatter dominates the stream. Their loggers are
  pinned to WARNING regardless of the tokenscope root level, so
  ``TOKENSCOPE_LOG_LEVEL=DEBUG`` doesn't drown the user in framework
  internals.
"""

from __future__ import annotations

import json
import logging
import os
import sys

_HANDLER_MARKER = "_tokenscope_handler"
_DEFAULT_LEVEL = "INFO"
_LEVEL_ENV_VAR = "TOKENSCOPE_LOG_LEVEL"
_FORMAT_ENV_VAR = "TOKENSCOPE_LOG_FORMAT"

# Single-line human format. Same shape every other Python tool uses
# so a developer can scan it without learning a tokenscope-specific
# syntax.
_HUMAN_FORMAT = "%(asctime)s %(levelname)-7s %(name)s | %(message)s"
_DATEFMT = "%Y-%m-%d %H:%M:%S"

# Third-party loggers whose internal chatter would otherwise dominate
# the stream at INFO level. Pinned to WARNING — they still surface
# real problems, just not lifecycle events.
_NOISY_THIRD_PARTY = (
    "streamlit",
    "watchdog",
    "urllib3",
    "tornado",
    "asyncio",
    "matplotlib",
)


class _JsonFormatter(logging.Formatter):
    """One JSON object per log record. Field set matches what
    ``docker logs ... | jq`` expects to find: timestamp, level
    name, logger name, message text. Exceptions emit a multi-line
    ``exc_info`` field; ``extra=`` kwargs propagate as
    top-level fields when callers use them.

    No external dependencies — the stdlib ``json`` module handles
    every field type we emit.
    """

    _RESERVED = frozenset(vars(logging.LogRecord("", 0, "", 0, None, None, None)))

    def format(self, record: logging.LogRecord) -> str:
        payload: dict = {
            "ts": self.formatTime(record, _DATEFMT),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        # Surface any caller-supplied `extra=` fields. This is the
        # one-line escape hatch for structured logging without
        # rebuilding the call sites: `log.info("foo", extra={"k": v})`
        # ends up as a top-level field in the JSON.
        for key, value in record.__dict__.items():
            if key in self._RESERVED or key.startswith("_"):
                continue
            if key in payload:
                continue
            try:
                json.dumps(value)
                payload[key] = value
            except (TypeError, ValueError):
                payload[key] = repr(value)
        if record.exc_info:
            payload["exc_info"] = self.formatException(record.exc_info)
        return json.dumps(payload, default=str)


def setup_logging(level: str | None = None) -> None:
    """Configure the ``tokenscope`` logger tree.

    Idempotent: re-running this on a subsequent Streamlit rerun is a
    no-op except for the level update — we still honour a changed env
    var or explicit arg without duplicating handlers.
    """
    resolved_level = _resolve_level(level)

    root = logging.getLogger("tokenscope")
    root.setLevel(resolved_level)
    # Don't bubble to the Python root — Streamlit installs its own root
    # handler that would otherwise print every tokenscope line twice.
    root.propagate = False

    if not any(getattr(h, _HANDLER_MARKER, False) for h in root.handlers):
        handler = logging.StreamHandler(sys.stdout)
        handler.setFormatter(_pick_formatter())
        setattr(handler, _HANDLER_MARKER, True)
        root.addHandler(handler)

    # Quieten third-party loggers AFTER tokenscope's tree is wired so
    # `TOKENSCOPE_LOG_LEVEL=DEBUG` doesn't surface Streamlit's
    # websocket-reconnect chatter at INFO.
    for noisy in _NOISY_THIRD_PARTY:
        logging.getLogger(noisy).setLevel(logging.WARNING)


def get_logger(name: str) -> logging.Logger:
    """Thin wrapper over `logging.getLogger`. Exists so callers import
    from `tokenscope.log` (single source of truth) rather than the
    stdlib — keeps the dependency edge explicit and lets us swap
    implementations later without ripping every import."""
    return logging.getLogger(name)


# --- format / level resolution -----------------------------------------


def _pick_formatter() -> logging.Formatter:
    """Resolve the active log formatter.

    Explicit ``TOKENSCOPE_LOG_FORMAT=json|human`` wins; otherwise
    auto-detect: TTY → human, no-TTY → JSON. The no-TTY case
    covers Docker (containers run without a controlling terminal
    by default) and any environment where stdout is piped — both
    benefit from JSON output that downstream tools can parse.
    """
    fmt = os.environ.get(_FORMAT_ENV_VAR, "").strip().lower()
    if fmt == "json":
        return _JsonFormatter()
    if fmt == "human":
        return logging.Formatter(_HUMAN_FORMAT, datefmt=_DATEFMT)
    if sys.stdout.isatty():
        return logging.Formatter(_HUMAN_FORMAT, datefmt=_DATEFMT)
    return _JsonFormatter()


def _resolve_level(level: str | None) -> int:
    """Explicit arg → env var → default. Returns the numeric level."""
    raw = level or os.environ.get(_LEVEL_ENV_VAR, "").strip() or _DEFAULT_LEVEL
    numeric = logging.getLevelName(raw.upper())
    if isinstance(numeric, int):
        return numeric
    # `getLevelName` returns "Level X" for unknown names — treat that
    # as junk and fall back to the documented default.
    return logging.INFO
