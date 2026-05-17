"""Unit tests for tokenscope logging.

Every log line introduced by the logging slice has a test here (or in
the per-module test file that already covers its host). The contract:
no log line ships without a test that proves it fires under the exact
condition documented.

The vehicle is pytest's `caplog` fixture, which captures every log
record with its module, level, and message — no monkey-patching of
handlers or formatters needed.

Coverage groups:
1. setup_logging mechanics — idempotency, level resolution, format.
2. External-boundary instrumentation — ccusage / pricing / tz / data.
3. Chart-drill instrumentation — the token-mix-bug diagnostic path.
4. User-click instrumentation — page selector, reset, drill buttons,
   breadcrumbs, family drill, sidebar state snapshot.
"""

from __future__ import annotations

import json
import logging
import subprocess
import urllib.error
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from tokenscope import ccusage, pricing, tz
from tokenscope.ccusage import CcusageError
from tokenscope.log import _HANDLER_MARKER, get_logger, setup_logging
from tokenscope.navigation import Navigation
from tokenscope.query import Query
from tokenscope.ui import _data, _nav, sidebar


# ---------- setup_logging mechanics ----------


@pytest.fixture(autouse=True)
def _logger_isolation(caplog):
    """Strip any previously-installed handlers and attach caplog's
    handler directly to the tokenscope logger.

    Production sets `propagate=False` so our logs don't bubble to the
    root logger (avoids double-printing alongside Python's lastResort
    handler). pytest's `caplog` fixture mounts its handler on root —
    so without this fixture, no test would see tokenscope records.

    Attaching caplog's handler directly to the tokenscope logger
    bridges the gap without weakening the production no-propagate
    guarantee.
    """
    root = logging.getLogger("tokenscope")
    original_handlers = list(root.handlers)
    original_propagate = root.propagate
    original_level = root.level
    root.handlers.clear()
    root.addHandler(caplog.handler)
    root.setLevel(logging.DEBUG)
    yield
    root.handlers.clear()
    for h in original_handlers:
        root.addHandler(h)
    root.propagate = original_propagate
    root.setLevel(original_level)


def test_setup_logging_installs_one_handler() -> None:
    setup_logging()
    root = logging.getLogger("tokenscope")
    tagged = [h for h in root.handlers if getattr(h, _HANDLER_MARKER, False)]
    assert len(tagged) == 1


def test_setup_logging_idempotent_under_streamlit_reruns() -> None:
    """Streamlit re-executes the app script on every interaction.
    Calling setup_logging() N times must result in exactly ONE handler,
    not N — otherwise each log line would print N times."""
    for _ in range(5):
        setup_logging()
    root = logging.getLogger("tokenscope")
    tagged = [h for h in root.handlers if getattr(h, _HANDLER_MARKER, False)]
    assert len(tagged) == 1


def test_setup_logging_default_level_is_info(monkeypatch) -> None:
    """Default level is INFO — verbose-by-default for a local-first
    dashboard. Quieten with `TOKENSCOPE_LOG_LEVEL=ERROR`; add detail
    with `TOKENSCOPE_LOG_LEVEL=DEBUG`."""
    monkeypatch.delenv("TOKENSCOPE_LOG_LEVEL", raising=False)
    setup_logging()
    assert logging.getLogger("tokenscope").level == logging.INFO


def test_setup_logging_env_var_overrides_default(monkeypatch) -> None:
    monkeypatch.setenv("TOKENSCOPE_LOG_LEVEL", "DEBUG")
    setup_logging()
    assert logging.getLogger("tokenscope").level == logging.DEBUG


def test_setup_logging_explicit_arg_beats_env_var(monkeypatch) -> None:
    monkeypatch.setenv("TOKENSCOPE_LOG_LEVEL", "DEBUG")
    setup_logging(level="ERROR")
    assert logging.getLogger("tokenscope").level == logging.ERROR


def test_setup_logging_junk_env_falls_back_to_default(monkeypatch) -> None:
    """Garbage in the env var falls back to the documented default
    (INFO) so a typo doesn't accidentally silence the stream."""
    monkeypatch.setenv("TOKENSCOPE_LOG_LEVEL", "NOT_A_LEVEL")
    setup_logging()
    assert logging.getLogger("tokenscope").level == logging.INFO


def test_setup_logging_env_var_quiets_to_error(monkeypatch) -> None:
    """The env var must work in both directions — DEBUG to add detail
    AND ERROR to quieten. Locks the bidirectional contract."""
    monkeypatch.setenv("TOKENSCOPE_LOG_LEVEL", "ERROR")
    setup_logging()
    assert logging.getLogger("tokenscope").level == logging.ERROR


def test_setup_logging_format_includes_module_name(caplog) -> None:
    setup_logging(level="DEBUG")
    log = get_logger("tokenscope.test_module")
    with caplog.at_level(logging.DEBUG, logger="tokenscope"):
        log.info("hello")
    # caplog.records gives us the record; the module name is what we
    # asserted carries through.
    assert any(r.name == "tokenscope.test_module" for r in caplog.records)


def test_setup_logging_does_not_propagate(monkeypatch) -> None:
    """tokenscope logs go to OUR stderr handler. They must NOT bubble
    to the root logger (which Streamlit configures with its own
    handler) — otherwise every line prints twice."""
    setup_logging(level="DEBUG")
    assert logging.getLogger("tokenscope").propagate is False


def test_setup_logging_handler_writes_to_stdout() -> None:
    """Docker contract: `docker logs <container>` surfaces stdout
    (and stderr) — but downstream tooling typically expects app
    output on stdout, so the handler writes there. A developer's
    terminal sees the same lines without any extra plumbing."""
    import sys
    setup_logging(level="DEBUG")
    root = logging.getLogger("tokenscope")
    tagged = [h for h in root.handlers if getattr(h, _HANDLER_MARKER, False)]
    assert len(tagged) == 1
    assert isinstance(tagged[0], logging.StreamHandler)
    assert tagged[0].stream is sys.stdout


def test_setup_logging_json_format_when_no_tty(monkeypatch) -> None:
    """Auto-detect: no TTY attached → JSON formatter. Covers the
    Docker / piped-stdout case where downstream tooling (e.g.
    `docker logs | jq`) expects one JSON record per line."""
    monkeypatch.delenv("TOKENSCOPE_LOG_FORMAT", raising=False)
    # Force isatty to False so the auto-detect picks JSON regardless
    # of whether the test runner happens to have a TTY.
    import sys
    monkeypatch.setattr(sys.stdout, "isatty", lambda: False, raising=False)
    setup_logging()
    root = logging.getLogger("tokenscope")
    tagged = next(h for h in root.handlers if getattr(h, _HANDLER_MARKER, False))
    from tokenscope.log import _JsonFormatter

    assert isinstance(tagged.formatter, _JsonFormatter)


def test_setup_logging_human_format_when_tty(monkeypatch) -> None:
    """Auto-detect: TTY attached → human-readable formatter. Covers
    the local-terminal case where a developer is reading logs
    directly."""
    monkeypatch.delenv("TOKENSCOPE_LOG_FORMAT", raising=False)
    import sys
    monkeypatch.setattr(sys.stdout, "isatty", lambda: True, raising=False)
    setup_logging()
    root = logging.getLogger("tokenscope")
    tagged = next(h for h in root.handlers if getattr(h, _HANDLER_MARKER, False))
    from tokenscope.log import _JsonFormatter

    assert not isinstance(tagged.formatter, _JsonFormatter)
    assert isinstance(tagged.formatter, logging.Formatter)


def test_setup_logging_explicit_json_format(monkeypatch) -> None:
    """`TOKENSCOPE_LOG_FORMAT=json` forces JSON even on a TTY —
    e.g. a developer wanting to pipe to `jq` from their terminal."""
    monkeypatch.setenv("TOKENSCOPE_LOG_FORMAT", "json")
    import sys
    monkeypatch.setattr(sys.stdout, "isatty", lambda: True, raising=False)
    setup_logging()
    root = logging.getLogger("tokenscope")
    tagged = next(h for h in root.handlers if getattr(h, _HANDLER_MARKER, False))
    from tokenscope.log import _JsonFormatter

    assert isinstance(tagged.formatter, _JsonFormatter)


def test_setup_logging_explicit_human_format(monkeypatch) -> None:
    """`TOKENSCOPE_LOG_FORMAT=human` forces human-readable even
    when not on a TTY — useful for `docker logs` viewers who
    prefer the readable form."""
    monkeypatch.setenv("TOKENSCOPE_LOG_FORMAT", "human")
    import sys
    monkeypatch.setattr(sys.stdout, "isatty", lambda: False, raising=False)
    setup_logging()
    root = logging.getLogger("tokenscope")
    tagged = next(h for h in root.handlers if getattr(h, _HANDLER_MARKER, False))
    from tokenscope.log import _JsonFormatter

    assert not isinstance(tagged.formatter, _JsonFormatter)


def test_json_formatter_emits_one_object_per_record() -> None:
    """The JSON formatter's output for a single record parses as
    one JSON object with the expected fields. Locks the downstream
    `jq` contract."""
    from tokenscope.log import _JsonFormatter

    record = logging.LogRecord(
        name="tokenscope.x", level=logging.INFO,
        pathname=__file__, lineno=1,
        msg="ccusage.ok argv=%s", args=(["daily"],),
        exc_info=None,
    )
    formatted = _JsonFormatter().format(record)
    parsed = json.loads(formatted)
    assert parsed["level"] == "INFO"
    assert parsed["logger"] == "tokenscope.x"
    assert "ccusage.ok" in parsed["message"]
    assert "ts" in parsed


def test_setup_logging_suppresses_noisy_third_party() -> None:
    """Streamlit / watchdog / urllib3 each log their own internal
    lifecycle at INFO/WARNING. On the Live view's 30s refresh
    cadence the websocket-reconnect chatter alone dominates the
    stream — pin them to WARNING so `TOKENSCOPE_LOG_LEVEL=DEBUG`
    doesn't drown the user in framework internals."""
    setup_logging(level="DEBUG")
    for noisy in ("streamlit", "watchdog", "urllib3"):
        assert logging.getLogger(noisy).level == logging.WARNING, (
            f"{noisy!r} logger not pinned to WARNING"
        )


# ---------- ccusage instrumentation ----------


def test_ccusage_check_installed_logs_error_when_missing(
    monkeypatch, tmp_path, caplog
) -> None:
    setup_logging(level="DEBUG")
    # Point CCUSAGE_BIN at a path that definitely doesn't exist.
    monkeypatch.setattr(ccusage, "CCUSAGE_BIN", tmp_path / "nonexistent-ccusage")
    with caplog.at_level(logging.DEBUG, logger="tokenscope"):
        with pytest.raises(CcusageError):
            ccusage._check_installed()
    assert any(
        "ccusage.not_installed" in r.message and r.levelno == logging.ERROR
        for r in caplog.records
    )


def test_ccusage_run_json_logs_start_and_ok(monkeypatch, caplog) -> None:
    setup_logging(level="DEBUG")
    # Point CCUSAGE_BIN at any extant path so _check_installed passes.
    monkeypatch.setattr(ccusage, "CCUSAGE_BIN", Path(__file__))
    monkeypatch.setattr(
        ccusage.subprocess,
        "run",
        lambda *a, **k: SimpleNamespace(
            stdout='{"daily": []}', stderr="", returncode=0
        ),
    )
    with caplog.at_level(logging.DEBUG, logger="tokenscope"):
        ccusage._run_json(["daily"])

    start = [r for r in caplog.records if "ccusage.start" in r.message]
    ok = [r for r in caplog.records if "ccusage.ok" in r.message]
    assert len(start) == 1 and start[0].levelno == logging.DEBUG
    # ccusage.ok is INFO — every subprocess invocation visible by
    # default. The data boundary cost us multiple debugging cycles
    # while it was DEBUG-only.
    assert len(ok) == 1 and ok[0].levelno == logging.INFO
    assert "elapsed_ms=" in ok[0].message
    assert "stdout_bytes=" in ok[0].message


def test_ccusage_run_json_logs_error_on_subprocess_failure(
    monkeypatch, caplog
) -> None:
    setup_logging(level="DEBUG")
    # Point CCUSAGE_BIN at any extant path so _check_installed passes.
    monkeypatch.setattr(ccusage, "CCUSAGE_BIN", Path(__file__))

    def _raise(*_a, **_k):
        raise subprocess.CalledProcessError(
            returncode=2, cmd=["ccusage"], stderr="boom"
        )

    monkeypatch.setattr(ccusage.subprocess, "run", _raise)
    with caplog.at_level(logging.DEBUG, logger="tokenscope"):
        with pytest.raises(CcusageError):
            ccusage._run_json(["daily"])

    errors = [
        r for r in caplog.records
        if "ccusage.exit_nonzero" in r.message and r.levelno == logging.ERROR
    ]
    assert len(errors) == 1
    assert "returncode=2" in errors[0].message
    assert "boom" in errors[0].message


def test_ccusage_run_json_logs_error_on_invalid_json(monkeypatch, caplog) -> None:
    setup_logging(level="DEBUG")
    # Point CCUSAGE_BIN at any extant path so _check_installed passes.
    monkeypatch.setattr(ccusage, "CCUSAGE_BIN", Path(__file__))
    monkeypatch.setattr(
        ccusage.subprocess,
        "run",
        lambda *a, **k: SimpleNamespace(
            stdout="not json", stderr="", returncode=0
        ),
    )
    with caplog.at_level(logging.DEBUG, logger="tokenscope"):
        with pytest.raises(CcusageError):
            ccusage._run_json(["daily"])

    bad = [
        r for r in caplog.records
        if "ccusage.bad_json" in r.message and r.levelno == logging.ERROR
    ]
    assert len(bad) == 1


# ---------- pricing instrumentation ----------


def test_pricing_fetch_logs_cache_fresh(monkeypatch, tmp_path, caplog) -> None:
    setup_logging(level="DEBUG")
    cache_file = tmp_path / "litellm_pricing.json"
    cache_file.write_text(json.dumps({"claude-opus-4-7": {}}))
    monkeypatch.setattr(pricing, "_CACHE_FILE", cache_file)
    with caplog.at_level(logging.DEBUG, logger="tokenscope"):
        result = pricing._fetch_pricing_json()
    assert result is not None
    fresh = [
        r for r in caplog.records
        if "pricing.cache.fresh" in r.message and r.levelno == logging.DEBUG
    ]
    assert len(fresh) == 1
    assert "age_seconds=" in fresh[0].message


def test_pricing_fetch_logs_warning_on_network_failure(
    monkeypatch, tmp_path, caplog
) -> None:
    setup_logging(level="DEBUG")
    cache_file = tmp_path / "litellm_pricing.json"
    monkeypatch.setattr(pricing, "_CACHE_FILE", cache_file)

    def _raise_urlerror(*_a, **_k):
        raise urllib.error.URLError("no network")

    monkeypatch.setattr(pricing.urllib.request, "urlopen", _raise_urlerror)
    with caplog.at_level(logging.DEBUG, logger="tokenscope"):
        result = pricing._fetch_pricing_json()
    assert result is None
    warns = [
        r for r in caplog.records
        if "pricing.fetch.failed" in r.message and r.levelno == logging.WARNING
    ]
    assert len(warns) == 1
    errs = [
        r for r in caplog.records
        if "pricing.unavailable" in r.message and r.levelno == logging.ERROR
    ]
    assert len(errs) == 1


# ---------- tz instrumentation ----------


def test_tz_logs_warning_on_invalid_tz_env(monkeypatch, caplog) -> None:
    """An invalid `TZ` env var (POSIX rule, absolute path, junk) is
    a misconfiguration the user benefits from seeing at WARNING —
    silently falling back to the OS probe is exactly the class of
    drift that produced the Live UTC bug ("page banner said EDT
    but charts ticked in UTC")."""
    setup_logging(level="INFO")
    monkeypatch.setenv("TZ", "EST5EDT,M3.2.0,M11.1.0")  # POSIX rule, not IANA
    # Force the symlink probe to a known IANA target so detection
    # succeeds and the function returns.
    monkeypatch.setattr(
        tz.Path, "is_symlink", lambda self: True, raising=False
    )
    monkeypatch.setattr(
        tz.os, "readlink",
        lambda _: "/usr/share/zoneinfo/America/Chicago",
    )
    with caplog.at_level(logging.WARNING, logger="tokenscope"):
        tz.detect_local_iana()
    invalid = [
        r for r in caplog.records
        if "tz.probe.env_invalid" in r.message and r.levelno == logging.WARNING
    ]
    assert len(invalid) == 1


def test_tz_logs_detected_zone_at_info(monkeypatch, caplog) -> None:
    """Every successful detection emits an INFO line with the
    resolved zone and the source it came from (env var / OS /
    /etc/localtime symlink). The Live UTC bug would have been a
    glance at this line — `source=env_var zone=UTC` is the smoking
    gun."""
    setup_logging(level="INFO")
    monkeypatch.setenv("TZ", "America/Chicago")
    with caplog.at_level(logging.INFO, logger="tokenscope"):
        result = tz.detect_local_iana()
    assert result == "America/Chicago"
    detected = [
        r for r in caplog.records
        if "tz.detected" in r.message and r.levelno == logging.INFO
    ]
    assert len(detected) == 1
    assert "zone=America/Chicago" in detected[0].message
    assert "source=env_var" in detected[0].message


def test_tz_logs_warning_on_full_fallback(monkeypatch, caplog) -> None:
    setup_logging(level="DEBUG")
    monkeypatch.delenv("TZ", raising=False)

    class _NoKey:
        def utcoffset(self, _):
            return None
        def tzname(self, _):
            return "X"
        def dst(self, _):
            return None

    class _FakeDt:
        @staticmethod
        def now():
            return SimpleNamespace(
                astimezone=lambda: SimpleNamespace(tzinfo=_NoKey())
            )

    monkeypatch.setattr(tz, "datetime", _FakeDt)
    monkeypatch.setattr(
        tz.Path, "is_symlink", lambda self: False, raising=False
    )
    with caplog.at_level(logging.DEBUG, logger="tokenscope"):
        result = tz.detect_local_iana()
    assert result == "UTC"
    warns = [
        r for r in caplog.records
        if "tz.fallback_to_utc" in r.message and r.levelno == logging.WARNING
    ]
    assert len(warns) == 1


# ---------- _data instrumentation ----------


def test_load_daily_logs_error_on_ccusage_failure(monkeypatch, caplog) -> None:
    setup_logging(level="DEBUG")
    from tokenscope.plans import get_plan
    from tokenscope.ui.sidebar import SidebarState
    from tokenscope import data

    def _raise(_q=None):
        raise CcusageError("simulated")

    monkeypatch.setattr(data, "daily", _raise)
    # We need a real SidebarState to feed load_daily, plus a stubbed
    # st.error since the function calls it.
    state = SidebarState(
        query=Query(), plan=get_plan("Enterprise"), selected_models=()
    )
    with patch("tokenscope.ui._data.st.error"):
        with caplog.at_level(logging.DEBUG, logger="tokenscope"):
            result = _data.load_daily(state)
    assert result is None
    errs = [
        r for r in caplog.records
        if "data.load.daily_failed" in r.message and r.levelno == logging.ERROR
    ]
    assert len(errs) == 1


# ---------- _nav chart-drill instrumentation (the token-mix bug path) ----------


@pytest.fixture
def _fake_streamlit_for_nav(monkeypatch):
    """Minimal stand-in so handle_chart_drill / route_to don't actually
    call into the Streamlit runtime."""
    state = {"params": {}, "reruns": 0}

    class _Params:
        def clear(self):
            state["params"].clear()
        def __setitem__(self, k, v):
            state["params"][k] = v
        def get(self, k, default=None):
            return state["params"].get(k, default)
        def __contains__(self, k):
            return k in state["params"]
        def __delitem__(self, k):
            del state["params"][k]

    monkeypatch.setattr(_nav.st, "query_params", _Params())
    monkeypatch.setattr(_nav.st, "rerun", lambda: state.update(reruns=state["reruns"] + 1))
    return state


def test_handle_chart_drill_logs_event_received_with_chart_key(
    _fake_streamlit_for_nav, caplog
) -> None:
    """The diagnostic line for the token-mix bug. When the user clicks
    a chart, this DEBUG line tells us (a) the click reached us and (b)
    which chart it was."""
    setup_logging(level="DEBUG")
    event = SimpleNamespace(
        selection=SimpleNamespace(points=[{"x": "2026-05-16T00:00:00"}])
    )
    nav = Navigation(view="overview")
    with caplog.at_level(logging.DEBUG, logger="tokenscope"):
        _nav.handle_chart_drill(
            event, lambda x: nav.to_day(x[:10]), chart_key="overview-token-mix"
        )
    received = [
        r for r in caplog.records
        if "chart.event.received" in r.message
        and "chart=overview-token-mix" in r.message
        and r.levelno == logging.DEBUG
    ]
    assert len(received) == 1


def test_handle_chart_drill_logs_no_event_at_debug(
    _fake_streamlit_for_nav, caplog
) -> None:
    """When the click DIDN'T reach us — event is None — the log records
    'has_event=False'. Absence of even this log for a given chart_key
    is the diagnostic for 'Plotly didn't fire selection at all'."""
    setup_logging(level="DEBUG")
    with caplog.at_level(logging.DEBUG, logger="tokenscope"):
        _nav.handle_chart_drill(
            None, Navigation.to_day, chart_key="overview-token-mix"
        )
    matches = [
        r for r in caplog.records
        if "chart.event.received" in r.message
        and "has_event=False" in r.message
        and "chart=overview-token-mix" in r.message
    ]
    assert len(matches) == 1


def test_handle_chart_drill_logs_empty_selection_at_debug(
    _fake_streamlit_for_nav, caplog
) -> None:
    setup_logging(level="DEBUG")
    event = SimpleNamespace(selection=None)
    with caplog.at_level(logging.DEBUG, logger="tokenscope"):
        _nav.handle_chart_drill(
            event, Navigation.to_day, chart_key="overview-token-mix"
        )
    matches = [
        r for r in caplog.records
        if "chart.event.empty_selection" in r.message
        and "chart=overview-token-mix" in r.message
    ]
    assert len(matches) == 1


def test_handle_chart_drill_logs_no_points_at_debug(
    _fake_streamlit_for_nav, caplog
) -> None:
    setup_logging(level="DEBUG")
    event = SimpleNamespace(selection=SimpleNamespace(points=[]))
    with caplog.at_level(logging.DEBUG, logger="tokenscope"):
        _nav.handle_chart_drill(
            event, Navigation.to_day, chart_key="overview-token-mix"
        )
    matches = [
        r for r in caplog.records
        if "chart.event.no_points" in r.message
        and "chart=overview-token-mix" in r.message
    ]
    assert len(matches) == 1


def test_handle_chart_drill_logs_info_on_successful_routing(
    _fake_streamlit_for_nav, caplog
) -> None:
    """End-to-end happy path: click → INFO log with chart_key and raw
    value. Paired with a nav.route INFO log from route_to."""
    setup_logging(level="DEBUG")
    event = SimpleNamespace(
        selection=SimpleNamespace(points=[{"x": "2026-05-16T00:00:00"}])
    )
    nav = Navigation(view="overview")
    with caplog.at_level(logging.DEBUG, logger="tokenscope"):
        _nav.handle_chart_drill(
            event, lambda x: nav.to_day(x[:10]), chart_key="overview-stacked-area"
        )
    drill = [
        r for r in caplog.records
        if "chart.drill" in r.message and r.levelno == logging.INFO
    ]
    assert len(drill) == 1
    assert "chart=overview-stacked-area" in drill[0].message
    assert "2026-05-16" in drill[0].message
    # And the subsequent route_to logs nav.route
    routes = [
        r for r in caplog.records
        if "nav.route" in r.message and r.levelno == logging.INFO
    ]
    assert len(routes) == 1


def test_route_to_logs_info_with_target(_fake_streamlit_for_nav, caplog) -> None:
    setup_logging(level="DEBUG")
    nav = Navigation(view="day", day="2026-05-16")
    with caplog.at_level(logging.INFO, logger="tokenscope"):
        _nav.route_to(nav)
    routes = [
        r for r in caplog.records
        if "nav.route" in r.message and r.levelno == logging.INFO
    ]
    assert len(routes) == 1
    assert "target=" in routes[0].message


# ---------- sidebar instrumentation ----------


def test_fetch_discovery_options_logs_warning_on_daily_failure(
    monkeypatch, caplog
) -> None:
    setup_logging(level="DEBUG")
    from tokenscope import data

    def _raise(_q=None):
        raise CcusageError("daily down")

    class _ProjReport:
        projects: dict = {}

    monkeypatch.setattr(data, "daily", _raise)
    monkeypatch.setattr(data, "daily_by_project", lambda _q=None: _ProjReport())
    with caplog.at_level(logging.WARNING, logger="tokenscope"):
        sidebar._fetch_discovery_options(Query())
    warns = [
        r for r in caplog.records
        if "sidebar.discovery.daily_failed" in r.message
        and r.levelno == logging.WARNING
    ]
    assert len(warns) == 1


def test_fetch_discovery_options_logs_warning_on_by_project_failure(
    monkeypatch, caplog
) -> None:
    setup_logging(level="DEBUG")
    from tokenscope import data

    class _DailyReport:
        daily: list = []

    def _raise(_q=None):
        raise CcusageError("by_project down")

    monkeypatch.setattr(data, "daily", lambda _q=None: _DailyReport())
    monkeypatch.setattr(data, "daily_by_project", _raise)
    with caplog.at_level(logging.WARNING, logger="tokenscope"):
        sidebar._fetch_discovery_options(Query())
    warns = [
        r for r in caplog.records
        if "sidebar.discovery.by_project_failed" in r.message
        and r.levelno == logging.WARNING
    ]
    assert len(warns) == 1


# ---------- user-click instrumentation via AppTest ----------


FIXTURES = Path(__file__).parent / "fixtures"


def _at(view: str | None = None, **extra_params):
    """Spin up an AppTest pointed at our app, with optional URL params."""
    from streamlit.testing.v1 import AppTest

    app_path = (
        Path(__file__).resolve().parent.parent / "src" / "tokenscope" / "app.py"
    )
    at = AppTest.from_file(str(app_path))
    if view is not None:
        at.query_params["view"] = view
    for k, v in extra_params.items():
        at.query_params[k] = v
    return at


_EMPTY_TOTALS = {
    "inputTokens": 0,
    "outputTokens": 0,
    "cacheCreationTokens": 0,
    "cacheReadTokens": 0,
    "totalTokens": 0,
    "totalCost": 0,
}


def _wire_minimal(mock_ccusage) -> None:
    """Register the smallest set of fixtures the app needs to render
    each view we touch in these tests.

    The `daily --instances` prefix must be registered separately and
    more specifically than `daily` — otherwise the daily.json fixture
    (shape: DailyReport) is also served for daily_by_project calls,
    which expect a DailyByProjectReport with a `projects` field.
    """
    mock_ccusage("daily", response=FIXTURES / "daily.json")
    mock_ccusage(
        "daily", "--instances",
        response={"projects": {}, "totals": dict(_EMPTY_TOTALS)},
    )
    mock_ccusage("session", response=FIXTURES / "session.json")
    mock_ccusage("blocks", response=FIXTURES / "blocks.json")


# AppTest runs `app.py:render`, which calls `setup_logging()` and resets
# the tokenscope logger level from the `TOKENSCOPE_LOG_LEVEL` env var
# (default WARNING). To capture DEBUG/INFO records emitted during the
# rerun, each AppTest case sets the env var before `.run()`. The
# autouse `_logger_isolation` fixture restores the level afterward.


def test_reset_button_logs_on_click(
    monkeypatch, mock_ccusage, mock_ccusage_version, caplog
) -> None:
    monkeypatch.setenv("TOKENSCOPE_LOG_LEVEL", "INFO")
    _wire_minimal(mock_ccusage)
    at = _at()
    at.run()
    reset_buttons = [b for b in at.button if b.key == "sidebar-reset"]
    assert reset_buttons, "no reset button found in sidebar"
    caplog.clear()
    reset_buttons[0].click().run()
    matches = [
        r for r in caplog.records
        if "sidebar.reset_clicked" in r.message and r.levelno == logging.INFO
    ]
    assert len(matches) == 1


def test_sidebar_state_snapshot_logged_at_debug(
    monkeypatch, mock_ccusage, mock_ccusage_version, caplog
) -> None:
    monkeypatch.setenv("TOKENSCOPE_LOG_LEVEL", "DEBUG")
    _wire_minimal(mock_ccusage)
    at = _at()
    at.run()
    state_logs = [
        r for r in caplog.records
        if "sidebar.state" in r.message and r.levelno == logging.DEBUG
    ]
    assert len(state_logs) >= 1
    msg = state_logs[-1].message
    for key in ("since=", "until=", "project=", "offline=", "models=", "plan="):
        assert key in msg, f"missing {key!r} in: {msg!r}"


def test_app_render_logs_view_at_debug(
    monkeypatch, mock_ccusage, mock_ccusage_version, caplog
) -> None:
    monkeypatch.setenv("TOKENSCOPE_LOG_LEVEL", "DEBUG")
    _wire_minimal(mock_ccusage)
    at = _at()
    at.run()
    matches = [
        r for r in caplog.records
        if "app.render" in r.message
        and "view=overview" in r.message
        and r.levelno == logging.DEBUG
    ]
    assert len(matches) >= 1


def test_day_entity_open_logs_on_session_button_click(
    monkeypatch, mock_ccusage, mock_ccusage_version, caplog
) -> None:
    """Clicking the Open-session button on the day view emits an INFO
    log with the button label so we can attribute the subsequent
    nav.route to a specific row."""
    monkeypatch.setenv("TOKENSCOPE_LOG_LEVEL", "INFO")
    _wire_minimal(mock_ccusage)
    at = _at("day", day="2026-04-05")
    at.run()
    session_buttons = [
        b for b in at.button if b.key and b.key.startswith("open-session-")
    ]
    assert session_buttons, "no Open-session buttons found on day view"
    caplog.clear()
    session_buttons[0].click().run()
    matches = [
        r for r in caplog.records
        if "day.entity_open" in r.message
        and "Open session" in r.message
        and r.levelno == logging.INFO
    ]
    assert len(matches) == 1


def test_breadcrumb_back_only_logs_on_click(
    monkeypatch, mock_ccusage, mock_ccusage_version, caplog
) -> None:
    """The "← Overview" fallback button on `?view=day` with no day
    param. Logs `breadcrumbs.back_only_clicked` on click."""
    monkeypatch.setenv("TOKENSCOPE_LOG_LEVEL", "INFO")
    _wire_minimal(mock_ccusage)
    at = _at("day")  # no `day` param triggers the back-only branch
    at.run()
    back_buttons = [b for b in at.button if b.key == "crumb-back-only"]
    assert back_buttons, "back-only breadcrumb not rendered"
    caplog.clear()
    back_buttons[0].click().run()
    matches = [
        r for r in caplog.records
        if "breadcrumbs.back_only_clicked" in r.message
        and r.levelno == logging.INFO
    ]
    assert len(matches) == 1
