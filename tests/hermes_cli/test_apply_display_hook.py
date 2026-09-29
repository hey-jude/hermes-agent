"""Tests for ``hermes_cli.plugins.apply_display_hook``.

The ``format_tool_result_for_display`` hook rewrites user-facing display text
(CLI completion lines, gateway progress/verbose) without touching the LLM
payload. These tests pin the dispatch contract the five fire sites rely on:
first string wins, non-strings are skipped, no hook means None, and a
misbehaving callback never breaks the turn.
"""

from __future__ import annotations

import hermes_cli.plugins as plugins


def test_first_string_wins(monkeypatch):
    monkeypatch.setattr(plugins, "has_hook", lambda name: True)
    monkeypatch.setattr(plugins, "invoke_hook", lambda name, **kw: [None, "first", "second"])
    assert plugins.apply_display_hook("terminal") == "first"


def test_non_string_results_skipped(monkeypatch):
    monkeypatch.setattr(plugins, "has_hook", lambda name: True)
    monkeypatch.setattr(plugins, "invoke_hook", lambda name, **kw: [None, 123, {"x": 1}])
    assert plugins.apply_display_hook("terminal") is None


def test_no_registered_hook_returns_none(monkeypatch):
    monkeypatch.setattr(plugins, "has_hook", lambda name: False)
    assert plugins.apply_display_hook("terminal") is None


def test_callback_error_never_raises(monkeypatch):
    """A crashing display callback must not break the tool-progress render."""
    monkeypatch.setattr(plugins, "has_hook", lambda name: True)

    def _boom(name, **kw):
        raise RuntimeError("boom")

    monkeypatch.setattr(plugins, "invoke_hook", _boom)
    assert plugins.apply_display_hook("terminal") is None


def test_kwargs_forwarded_to_hook(monkeypatch):
    seen = {}
    monkeypatch.setattr(plugins, "has_hook", lambda name: True)

    def _capture(name, **kw):
        seen.update(kw)
        return []

    monkeypatch.setattr(plugins, "invoke_hook", _capture)
    plugins.apply_display_hook("write_file", args={"path": "a"}, result="r",
                               display_context="gateway_progress")
    assert seen == {"tool_name": "write_file", "args": {"path": "a"}, "result": "r",
                    "display_context": "gateway_progress"}
