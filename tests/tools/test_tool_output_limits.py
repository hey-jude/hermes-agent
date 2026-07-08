"""Tests for tools.tool_output_limits.

Covers:
1. Default values when no config is provided.
2. Config override picks up user-supplied max_bytes / max_lines /
   max_line_length.
3. Malformed values (None, negative, wrong type) fall back to defaults
   rather than raising.
4. Integration: the helpers return what the terminal_tool and
   file_operations call paths will actually consume.

Port-tracking: anomalyco/opencode PR #23770
(feat(truncate): allow configuring tool output truncation limits).
"""

from __future__ import annotations

import json
from unittest.mock import patch

import pytest

from tools import tool_output_limits as tol


@pytest.fixture(autouse=True)
def _reset_limits_cache():
    """get_tool_output_limits() now memoizes its result for the process
    lifetime, so each test must start from a clean cache to observe the
    config value it patches in."""
    tol._reset_tool_output_limits_cache()
    yield
    tol._reset_tool_output_limits_cache()


class TestDefaults:
    def test_defaults_match_previous_hardcoded_values(self):
        assert tol.DEFAULT_MAX_BYTES == 50_000
        assert tol.DEFAULT_MAX_LINES == 2000
        assert tol.DEFAULT_MAX_LINE_LENGTH == 2000
        assert tol.DEFAULT_FORMAT == "json"

    def test_get_limits_returns_defaults_when_config_missing(self):
        with patch("hermes_cli.config.load_config", return_value={}):
            limits = tol.get_tool_output_limits()
        assert limits == {
            "max_bytes": tol.DEFAULT_MAX_BYTES,
            "max_lines": tol.DEFAULT_MAX_LINES,
            "max_line_length": tol.DEFAULT_MAX_LINE_LENGTH,
            "format": "json",
        }

    def test_get_limits_returns_defaults_when_config_not_a_dict(self):
        # load_config should always return a dict but be defensive anyway.
        with patch("hermes_cli.config.load_config", return_value="not a dict"):
            limits = tol.get_tool_output_limits()
        assert limits["max_bytes"] == tol.DEFAULT_MAX_BYTES

    def test_get_limits_returns_defaults_when_load_config_raises(self):
        def _boom():
            raise RuntimeError("boom")

        with patch("hermes_cli.config.load_config", side_effect=_boom):
            limits = tol.get_tool_output_limits()
        assert limits["max_lines"] == tol.DEFAULT_MAX_LINES


class TestOverrides:
    def test_user_config_overrides_all_three(self):
        cfg = {
            "tool_output": {
                "max_bytes": 100_000,
                "max_lines": 5000,
                "max_line_length": 4096,
            }
        }
        with patch("hermes_cli.config.load_config", return_value=cfg):
            limits = tol.get_tool_output_limits()
        assert limits["max_bytes"] == 100_000
        assert limits["max_lines"] == 5000
        assert limits["max_line_length"] == 4096
        assert limits["format"] == "json"

    def test_partial_override_preserves_other_defaults(self):
        cfg = {"tool_output": {"max_bytes": 200_000}}
        with patch("hermes_cli.config.load_config", return_value=cfg):
            limits = tol.get_tool_output_limits()
        assert limits["max_bytes"] == 200_000
        assert limits["max_lines"] == tol.DEFAULT_MAX_LINES
        assert limits["max_line_length"] == tol.DEFAULT_MAX_LINE_LENGTH


    def test_section_not_a_dict_falls_back(self):
        cfg = {"tool_output": "nonsense"}
        with patch("hermes_cli.config.load_config", return_value=cfg):
            limits = tol.get_tool_output_limits()
        assert limits["max_bytes"] == tol.DEFAULT_MAX_BYTES


class TestCoercion:
    @pytest.mark.parametrize("bad", [None, "not a number", -1, 0, [], {}])
    def test_invalid_values_fall_back_to_defaults(self, bad):
        cfg = {"tool_output": {"max_bytes": bad, "max_lines": bad, "max_line_length": bad}}
        with patch("hermes_cli.config.load_config", return_value=cfg):
            limits = tol.get_tool_output_limits()
        assert limits["max_bytes"] == tol.DEFAULT_MAX_BYTES
        assert limits["max_lines"] == tol.DEFAULT_MAX_LINES
        assert limits["max_line_length"] == tol.DEFAULT_MAX_LINE_LENGTH

    def test_string_integer_is_coerced(self):
        cfg = {"tool_output": {"max_bytes": "75000"}}
        with patch("hermes_cli.config.load_config", return_value=cfg):
            limits = tol.get_tool_output_limits()
        assert limits["max_bytes"] == 75_000


class TestShortcuts:
    def test_individual_accessors_delegate_to_get_tool_output_limits(self):
        cfg = {
            "tool_output": {
                "max_bytes": 111,
                "max_lines": 222,
                "max_line_length": 333,
            }
        }
        with patch("hermes_cli.config.load_config", return_value=cfg):
            assert tol.get_max_bytes() == 111
            assert tol.get_max_lines() == 222
            assert tol.get_max_line_length() == 333


class TestDefaultConfigHasSection:
    """The DEFAULT_CONFIG in hermes_cli.config must expose tool_output so
    that ``hermes setup`` and default installs stay in sync with the
    helpers here."""

    def test_default_config_contains_tool_output_section(self):
        from hermes_cli.config import DEFAULT_CONFIG
        assert "tool_output" in DEFAULT_CONFIG
        section = DEFAULT_CONFIG["tool_output"]
        assert isinstance(section, dict)
        assert section["max_bytes"] == tol.DEFAULT_MAX_BYTES
        assert section["max_lines"] == tol.DEFAULT_MAX_LINES
        assert section["max_line_length"] == tol.DEFAULT_MAX_LINE_LENGTH


class TestIntegrationReadPagination:
    """normalize_read_pagination uses get_max_lines() — verify the plumbing."""

    def test_pagination_limit_clamped_by_config_value(self):
        from tools.file_operations import normalize_read_pagination
        cfg = {"tool_output": {"max_lines": 50}}
        with patch("hermes_cli.config.load_config", return_value=cfg):
            offset, limit = normalize_read_pagination(offset=1, limit=1000)
        # limit should have been clamped to 50 (the configured max_lines)
        assert limit == 50
        assert offset == 1

    def test_pagination_default_when_config_missing(self):
        from tools.file_operations import normalize_read_pagination
        with patch("hermes_cli.config.load_config", return_value={}):
            offset, limit = normalize_read_pagination(offset=10, limit=100000)
        # Clamped to default MAX_LINES (2000).
        assert limit == tol.DEFAULT_MAX_LINES
        assert offset == 10


class TestNormalizeNewlines:
    """Literal \\n / \\t escapes in terminal output should become real characters."""

    def test_literal_backslash_n_becomes_newline(self):
        from plugins.tool_output_format import _normalize_newlines
        assert _normalize_newlines("line1\\nline2") == "line1\nline2"

    def test_literal_backslash_t_becomes_tab(self):
        from plugins.tool_output_format import _normalize_newlines
        assert _normalize_newlines("col1\\tcol2") == "col1\tcol2"

    def test_real_newlines_unchanged(self):
        from plugins.tool_output_format import _normalize_newlines
        assert _normalize_newlines("line1\nline2") == "line1\nline2"

    def test_mixed_escapes(self):
        from plugins.tool_output_format import _normalize_newlines
        assert _normalize_newlines("a\\nb\\tc") == "a\nb\tc"

    def test_no_escapes_unchanged(self):
        from plugins.tool_output_format import _normalize_newlines
        assert _normalize_newlines("plain text") == "plain text"

    def test_double_backslash_not_confused(self):
        from plugins.tool_output_format import _normalize_newlines
        # \\\\n means literal backslash followed by n, not newline
        assert _normalize_newlines("path\\\\nfile") == "path\\nfile"


class TestTerminalOutputExtraction:
    """Terminal tool returns {"output": ..., "exit_code": ..., "error": ...}
    but display formatting should extract just the output field."""

    def test_terminal_output_extracted_for_yaml(self):
        from plugins.tool_output_format import _extract_display_payload
        terminal_result = json.dumps({
            "output": "total 48\ndrwxr-xr-x  12 jude  staff  384 Jun 20 12:30 .",
            "exit_code": 0,
            "error": None,
        })
        payload = _extract_display_payload(terminal_result, "terminal")
        assert payload == "total 48\ndrwxr-xr-x  12 jude  staff  384 Jun 20 12:30 ."

    def test_terminal_error_result_extracted(self):
        from plugins.tool_output_format import _extract_display_payload
        terminal_result = json.dumps({
            "output": "ls: no such file or directory",
            "exit_code": 2,
            "error": None,
        })
        payload = _extract_display_payload(terminal_result, "terminal")
        assert payload == "ls: no such file or directory"

    def test_non_terminal_tool_passthrough(self):
        from plugins.tool_output_format import _extract_display_payload
        file_result = json.dumps({"content": "hello", "total_lines": 1})
        payload = _extract_display_payload(file_result, "read_file")
        assert payload == file_result  # unchanged

    def test_terminal_non_json_passthrough(self):
        from plugins.tool_output_format import _extract_display_payload
        raw = "just plain text output"
        payload = _extract_display_payload(raw, "terminal")
        assert payload == raw  # not JSON, pass through

    def test_terminal_missing_output_field_passthrough(self):
        from plugins.tool_output_format import _extract_display_payload
        terminal_result = json.dumps({"exit_code": 0, "error": None})
        payload = _extract_display_payload(terminal_result, "terminal")
        assert payload == terminal_result  # no "output" key, pass through

    def test_terminal_output_yaml_format(self):
        from plugins.tool_output_format import _on_format_tool_result_for_display
        terminal_result = json.dumps({
            "output": "file1.txt\nfile2.txt",
            "exit_code": 0,
            "error": None,
        })
        with patch("plugins.tool_output_format.get_tool_output_format", return_value="yaml"):
            result = _on_format_tool_result_for_display(
                tool_name="terminal", result=terminal_result
            )
        assert result is not None
        assert "file1.txt" in result
        assert "exit_code" not in result  # metadata stripped

    def test_terminal_output_text_format(self):
        from plugins.tool_output_format import _on_format_tool_result_for_display
        terminal_result = json.dumps({
            "output": "hello world",
            "exit_code": 0,
            "error": None,
        })
        with patch("plugins.tool_output_format.get_tool_output_format", return_value="text"):
            result = _on_format_tool_result_for_display(
                tool_name="terminal", result=terminal_result
            )
        assert result == "hello world"


# --- Part 1: literal block scalar for multi-line YAML values ---

class TestLiteralBlockScalar:
    def test_multiline_value_uses_block_scalar(self):
        args = {"content": "def f():\n    return 1\n", "path": "/tmp/x.py"}
        out = tol.format_args_for_display(args, fmt="yaml")
        assert "|" in out  # block scalar indicator
        assert '"def f()' not in out  # not double-quoted one-liner
        assert "def f():" in out
        assert "    return 1" in out

    def test_single_line_value_stays_inline(self):
        args = {"path": "/tmp/x.py"}
        out = tol.format_args_for_display(args, fmt="yaml")
        assert "|" not in out.split("\n", 1)[0]

    def test_nested_multiline_dict_blockifies(self):
        result = {"content": "a\nb"}
        out = tol.format_tool_result_for_display(result, fmt="yaml")
        assert "|" in out
        assert "a" in out and "b" in out


# --- Part 2: boxed-tool fenced progress display (plugin) ---

class TestBoxedToolFence:
    def test_write_file_fenced(self):
        from plugins.tool_output_format import _on_format_tool_result_for_display
        args = {"content": "def f():\n    return 1\n", "path": "/tmp/x.py"}
        with patch("plugins.tool_output_format.get_tool_output_format", return_value="yaml"):
            out = _on_format_tool_result_for_display(
                tool_name="write_file",
                args=args,
                display_context="gateway_progress",
            )
        assert out is not None
        assert "content:" in out
        assert out.count("```") >= 2
        assert "def f():" in out

    def test_patch_fenced_two_fields(self):
        from plugins.tool_output_format import _on_format_tool_result_for_display
        args = {"old_string": "a\nb", "new_string": "c\nd"}
        with patch("plugins.tool_output_format.get_tool_output_format", return_value="yaml"):
            out = _on_format_tool_result_for_display(
                tool_name="patch",
                args=args,
                display_context="gateway_progress",
            )
        assert out is not None
        assert "old_string:" in out
        assert "new_string:" in out
        assert out.count("```") == 4

    def test_non_fenced_args_shown_above_fence(self):
        from plugins.tool_output_format import _on_format_tool_result_for_display
        args = {
            "content": "def f():\n    return 1\n",
            "path": "/tmp/x.py",
            "mode": "overwrite",
            "replace_all": False,
        }
        with patch("plugins.tool_output_format.get_tool_output_format", return_value="yaml"):
            out = _on_format_tool_result_for_display(
                tool_name="write_file",
                args=args,
                display_context="gateway_progress",
            )
        assert out is not None
        # Boxed field still fenced
        assert "content:" in out
        assert out.count("```") >= 2
        # Non-fenced args surfaced below the fence
        assert "path: `/tmp/x.py`" in out
        assert "mode: `overwrite`" in out
        assert "replace_all: False" in out
        assert "replace_all: False" in out
        assert out.index("path: `/tmp/x.py`") < out.index("```")

    def test_patch_mode_renders_patch_field_as_fenced(self):
        from plugins.tool_output_format import _on_format_tool_result_for_display

        patch_text = (
            "*** Begin Patch\n*** Update File: a.py\n@@\n-old\n+new\n*** End Patch"
        )
        args = {"mode": "patch", "patch": patch_text}
        with patch(
            "plugins.tool_output_format.get_tool_output_format",
            return_value="yaml",
        ):
            out = _on_format_tool_result_for_display(
                tool_name="patch",
                args=args,
                display_context="gateway_progress",
            )
        assert out is not None
        # Full patch payload must be a fenced block, NOT collapsed into an
        # inline-code summary line.
        assert "```" in out
        assert "*** Begin Patch" in out
        # mode summary sits above the fenced patch payload.
        assert "mode: `patch`" in out
        assert out.index("mode: `patch`") < out.index("*** Begin Patch")

    def test_non_boxed_tool_returns_none(self):
        from plugins.tool_output_format import _on_format_tool_result_for_display
        with patch("plugins.tool_output_format.get_tool_output_format", return_value="yaml"):
            out = _on_format_tool_result_for_display(
                tool_name="search_files",
                args={"pattern": "x"},
                display_context="gateway_progress",
            )
        assert out is None

    def test_terminal_not_boxed_in_progress(self):
        from plugins.tool_output_format import _on_format_tool_result_for_display
        with patch("plugins.tool_output_format.get_tool_output_format", return_value="yaml"):
            out = _on_format_tool_result_for_display(
                tool_name="terminal",
                args={"command": "ls"},
                display_context="gateway_progress",
            )
        assert out is None
