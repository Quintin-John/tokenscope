"""Unit tests for tokenscope.app — the entry-point module.

End-to-end rendering of every view is covered by `test_ui_smoke.py`.
This file holds focused tests on the pure helpers in `app.py` that
don't need a full Streamlit runtime:

- `_build_about_text` — pure prose builder.
- `main` — console-script entry: re-execs under `streamlit run`. The
  argv it builds pins security-relevant defaults
  (`--server.address=127.0.0.1`, telemetry-disable) that no
  integration test exercises (the suite invokes app via `AppTest`,
  bypassing `main` entirely).
- `_streamlit_runtime_active` — defensive ImportError branch for
  hosts where the streamlit package isn't installed.
"""

from __future__ import annotations

import sys

import pytest

from tokenscope import app, ccusage


def test_build_about_text_includes_version_on_success(monkeypatch) -> None:
    """Happy path: when `get_ccusage_version` returns a string, the
    About blurb surfaces it verbatim (no inline-code styling).

    Note: `app.py` imports `get_ccusage_version` by name at module
    load, so we patch the binding inside `app`, not the source module.
    """
    monkeypatch.setattr(app, "get_ccusage_version", lambda: "18.0.11")
    text = app._build_about_text()
    assert "ccusage version: 18.0.11" in text
    assert "`" not in text.split("ccusage version:")[1].split("\n")[0], (
        "version should be plain text, not wrapped in backticks"
    )


def test_build_about_text_renders_fallback_on_ccusage_error(monkeypatch) -> None:
    """When ccusage isn't installed or its `--version` shell-out
    fails, the About blurb still renders — the page-config call
    can't propagate the error."""
    def _raise() -> str:
        raise ccusage.CcusageError("simulated bridge failure")

    monkeypatch.setattr(app, "get_ccusage_version", _raise)
    text = app._build_about_text()
    assert "ccusage version: unavailable" in text


def test_build_about_text_mentions_tz_override() -> None:
    """The blurb is where the `TZ` env-var instruction lives — moved
    out of the sidebar caption so the panel doesn't read as CLI docs."""
    # No monkeypatch — uses whatever ccusage version is on disk, which
    # we don't care about for this assertion.
    text = app._build_about_text()
    assert "TZ" in text
    assert "timezone" in text.lower()


# --- Slice H: main() console-script entry coverage ----------------------
#
# `main()` is the entry point for the `tokenscope` CLI command (see
# `pyproject.toml [project.scripts]`). Every user invocation runs it
# unchanged. It builds an argv list and re-execs the script under
# `streamlit run`. Pre-Slice-H this function had ZERO test coverage —
# the integration suite uses `AppTest` which bypasses `main` entirely.
#
# Three security/operational defaults the function bakes in must be
# pinned by tests so a future edit can't silently regress them:
#
#   * `--server.address=127.0.0.1` — local-only binding (README
#     claims "local-first"; a regression to `0.0.0.0` would expose
#     the dashboard to the network).
#   * `--browser.gatherUsageStats=false` — Streamlit telemetry off
#     (silent removal would start phoning home).
#   * Pass-through of `sys.argv[1:]` — user-supplied flags reach
#     streamlit unchanged.


def test_main_re_execs_streamlit_with_localhost_binding_and_no_telemetry(
    monkeypatch,
) -> None:
    """`main()` builds an argv with the security-relevant defaults
    documented above. Any future regression that dropped or changed
    these flags silently surfaces here as a missing-flag assertion
    failure."""
    captured: list[list[str]] = []
    monkeypatch.setattr(
        app.subprocess, "call",
        lambda argv: (captured.append(argv), 0)[1],
    )
    monkeypatch.setattr(sys, "argv", ["tokenscope"])

    with pytest.raises(SystemExit) as exc_info:
        app.main()

    assert exc_info.value.code == 0
    assert len(captured) == 1, (
        f"expected exactly one subprocess.call; got {len(captured)}"
    )
    argv = captured[0]

    # The security/privacy defaults — load-bearing.
    assert "--server.address=127.0.0.1" in argv, (
        f"missing localhost binding (--server.address=127.0.0.1); "
        f"argv={argv!r}"
    )
    assert "--browser.gatherUsageStats=false" in argv, (
        f"missing telemetry-disable flag; argv={argv!r}"
    )

    # The streamlit subcommand shape.
    assert argv[0] == sys.executable
    assert argv[1:4] == ["-m", "streamlit", "run"]
    # The fourth positional argv slot is the script path — match by
    # filename rather than absolute path so the test is portable.
    assert argv[4].endswith("app.py"), (
        f"expected the streamlit run target to be app.py; got {argv[4]!r}"
    )


def test_main_passes_through_extra_argv_to_streamlit(monkeypatch) -> None:
    """`*sys.argv[1:]` is appended to the streamlit command so users
    can pass `--server.port=8080` etc. on the `tokenscope` CLI
    invocation. A regression that dropped the splat would silently
    ignore every user-supplied flag."""
    captured: list[list[str]] = []
    monkeypatch.setattr(
        app.subprocess, "call",
        lambda argv: (captured.append(argv), 0)[1],
    )
    monkeypatch.setattr(
        sys, "argv",
        ["tokenscope", "--server.port=8080", "--logger.level=debug"],
    )

    with pytest.raises(SystemExit):
        app.main()

    argv = captured[0]
    assert "--server.port=8080" in argv
    assert "--logger.level=debug" in argv


def test_main_propagates_subprocess_exit_code(monkeypatch) -> None:
    """A non-zero exit code from streamlit must propagate as the
    tokenscope process's exit code, not be swallowed. The function
    uses `raise SystemExit(subprocess.call(...))` for this; a
    regression to `subprocess.call(...); raise SystemExit(0)` would
    silently mask streamlit crashes."""
    monkeypatch.setattr(app.subprocess, "call", lambda _argv: 42)
    monkeypatch.setattr(sys, "argv", ["tokenscope"])

    with pytest.raises(SystemExit) as exc_info:
        app.main()

    assert exc_info.value.code == 42


# --- Slice H: _streamlit_runtime_active ImportError branch ---------------


def test_streamlit_runtime_active_returns_false_when_module_missing(
    monkeypatch,
) -> None:
    """The defensive `except ImportError: return False` branch fires
    when `streamlit.runtime` can't be imported (e.g. running the
    script from a checkout that hasn't been `pip install`'d).
    The function returns False so the bottom-of-module
    `if _streamlit_runtime_active(): render()` short-circuits and
    importing the module is side-effect-free.

    Forces ImportError via `sys.modules[name] = None` — the
    documented way to make `import name` raise
    `ModuleNotFoundError` (an `ImportError` subclass)."""
    monkeypatch.setitem(sys.modules, "streamlit.runtime", None)

    assert app._streamlit_runtime_active() is False
