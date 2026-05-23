"""Tests for tokenscope.ui._data — shared data-loading glue.

The loaders themselves are exercised end-to-end via AppTest in
tests/test_ui_smoke.py; this file pins the module's shared constants —
specifically the single-source empty-window copy that every
window-scoped view renders.
"""

from __future__ import annotations

import pytest

from tokenscope.ui import _data, cache, daily, models, overview


def test_empty_window_message_content() -> None:
    """The shared empty-window copy names the two recovery actions
    (widen Date range, clear Project) with markdown emphasis on the
    sidebar control names."""
    msg = _data.EMPTY_WINDOW_MESSAGE
    assert msg.startswith("No usage in the selected window.")
    assert "**Date range**" in msg
    assert "**Project**" in msg


@pytest.mark.parametrize("view", [overview, cache, models, daily])
def test_views_share_one_empty_window_message(view) -> None:
    """Every window-scoped view references the SAME constant object, so
    the wording can never drift between views (the duplication this
    consolidation removed)."""
    assert view.EMPTY_WINDOW_MESSAGE is _data.EMPTY_WINDOW_MESSAGE
