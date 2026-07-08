"""Configurable tool-output truncation limits and display formatting.

Ported from anomalyco/opencode PR #23770 (``feat(truncate): allow
configuring tool output truncation limits``).

OpenCode hardcoded ``MAX_LINES = 2000`` and ``MAX_BYTES = 50 * 1024``
as tool-output truncation thresholds. Hermes-agent had the same
hardcoded constants in two places:

* ``tools/terminal_tool.py`` — ``MAX_OUTPUT_CHARS = 50000`` (terminal
  stdout/stderr cap)
* ``tools/file_operations.py`` — ``MAX_LINES = 2000`` /
  ``MAX_LINE_LENGTH = 2000`` (read_file pagination cap + per-line cap)

This module centralises those values behind a single config section
(``tool_output`` in ``config.yaml``) so power users can tune them
without patching the source. The existing hardcoded numbers remain as
defaults, so behaviour is unchanged when the config key is absent.

Example ``config.yaml``::

    tool_output:
      max_bytes: 100000        # terminal output cap (chars)
      max_lines: 5000          # read_file pagination + truncation cap
      max_line_length: 2000    # per-line length cap before '... [truncated]'
      format: yaml             # json | yaml | text | compact — display only

The limits reader is defensive: any error (missing config file, invalid
value type, etc.) falls back to the built-in defaults so tools never
fail because of a malformed config.
"""

from __future__ import annotations

import json
from typing import Any, Dict

# Hardcoded defaults — these match the pre-existing values, so adding
# this module is behaviour-preserving for users who don't set
# ``tool_output`` in config.yaml.
DEFAULT_MAX_BYTES = 50_000       # terminal_tool.MAX_OUTPUT_CHARS
DEFAULT_MAX_LINES = 2000         # file_operations.MAX_LINES
DEFAULT_MAX_LINE_LENGTH = 2000   # file_operations.MAX_LINE_LENGTH
DEFAULT_FORMAT = "json"
ALLOWED_FORMATS = frozenset({"json", "yaml", "text", "compact"})

# Literal-block scalar support for YAML display.
#
# PyYAML emits strings containing newlines as double-quoted `"a\nb"` by
# default, which is unreadable for multi-line tool args (write_file content,
# execute_code code, patch old/new_string).  Wrapping such strings in
# ``_LiteralStr`` makes PyYAML emit a literal block scalar (``|``) instead,
# preserving real line breaks.
class _LiteralStr(str):
    """Marker type — PyYAML emits it as a literal block scalar (``|``)."""


def _literal_representer(dumper, data):
    return dumper.represent_scalar("tag:yaml.org,2002:str", data, style="|")


def _register_yaml_literal() -> None:
    """Register the ``_LiteralStr`` representer once (idempotent)."""
    try:
        import yaml
        yaml.add_representer(_LiteralStr, _literal_representer)
    except (ImportError, TypeError):
        pass


_register_yaml_literal()


def _blockify(obj: Any) -> Any:
    """Recursively wrap any string containing a newline in ``_LiteralStr``.

    Applied before ``yaml.dump`` so multi-line values render as literal
    block scalars instead of escaped ``"\\n"`` one-liners.  Non-str and
    newline-free str values pass through unchanged.
    """
    if isinstance(obj, str):
        return _LiteralStr(obj) if "\n" in obj else obj
    if isinstance(obj, dict):
        return {k: _blockify(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return type(obj)(_blockify(v) for v in obj)
    return obj


# Module-level cache — populated on first call.
# Avoids repeated config file I/O on every tool call.
_cached_limits: dict | None = None


def _coerce_positive_int(value: Any, default: int) -> int:
    """Return ``value`` as a positive int, or ``default`` on any issue."""
    try:
        iv = int(value)
    except (TypeError, ValueError):
        return default
    if iv <= 0:
        return default
    return iv


def _resolve_format(value: Any) -> str:
    """Return ``value`` as a valid format string, or ``DEFAULT_FORMAT``."""
    if isinstance(value, str) and value in ALLOWED_FORMATS:
        return value
    return DEFAULT_FORMAT


def get_tool_output_limits() -> Dict[str, Any]:
    """Return resolved tool-output limits, reading ``tool_output`` from config.

    Keys: ``max_bytes``, ``max_lines``, ``max_line_length``, ``format``.
    Missing or invalid entries fall through to the ``DEFAULT_*`` constants.
    This function NEVER raises.

    Result is cached for the process lifetime to avoid repeated disk I/O
    on every tool call. Call ``_reset_tool_output_limits_cache()`` in
    tests that need a fresh read after config changes.
    """
    global _cached_limits
    if _cached_limits is not None:
        return _cached_limits
    try:
        from hermes_cli.config import load_config
        cfg = load_config() or {}
        section = cfg.get("tool_output") if isinstance(cfg, dict) else None
        if not isinstance(section, dict):
            section = {}
    except Exception:
        section = {}

    _cached_limits = {
        "max_bytes": _coerce_positive_int(section.get("max_bytes"), DEFAULT_MAX_BYTES),
        "max_lines": _coerce_positive_int(section.get("max_lines"), DEFAULT_MAX_LINES),
        "max_line_length": _coerce_positive_int(
            section.get("max_line_length"), DEFAULT_MAX_LINE_LENGTH
        ),
        "format": _resolve_format(section.get("format")),
    }
    return _cached_limits


def _reset_tool_output_limits_cache() -> None:
    """Reset the cached limits — for tests or after config hot-reload."""
    global _cached_limits
    _cached_limits = None


def get_max_bytes() -> int:
    """Shortcut for terminal-tool callers that only need the byte cap."""
    return get_tool_output_limits()["max_bytes"]


def get_max_lines() -> int:
    """Shortcut for file-ops callers that only need the line cap."""
    return get_tool_output_limits()["max_lines"]


def get_max_line_length() -> int:
    """Shortcut for file-ops callers that only need the per-line cap."""
    return get_tool_output_limits()["max_line_length"]


def get_tool_output_format() -> str:
    """Shortcut for display callers that only need the format setting."""
    return get_tool_output_limits()["format"]


def format_tool_result_for_display(result: Any, fmt: str | None = None) -> str:
    """Format a tool result for user display. Do NOT use for LLM paths."""
    if fmt is None:
        fmt = get_tool_output_format()

    if fmt == "yaml":
        try:
            import yaml
        except ImportError:
            return str(result) if isinstance(result, str) else json.dumps(result, indent=2, default=str)
        if isinstance(result, str):
            try:
                parsed = json.loads(result)
                return yaml.dump(_blockify(parsed), default_flow_style=False, allow_unicode=True, width=120)
            except (json.JSONDecodeError, TypeError):
                return result
        return yaml.dump(_blockify(result), default_flow_style=False, allow_unicode=True, width=120)

    if fmt == "compact":
        if isinstance(result, str):
            try:
                parsed = json.loads(result)
                return json.dumps(parsed, indent=2, default=str)
            except (json.JSONDecodeError, TypeError):
                return result
        return json.dumps(result, indent=2, default=str)

    if fmt == "text":
        return str(result)

    # json (default)
    return str(result) if isinstance(result, str) else json.dumps(result, indent=2, default=str)


def format_args_for_display(args: dict, fmt: str | None = None) -> str:
    """Format tool call arguments for user display. Do NOT use for LLM paths."""
    if fmt is None:
        fmt = get_tool_output_format()

    if fmt == "yaml":
        try:
            import yaml
        except ImportError:
            return json.dumps(args, ensure_ascii=False, default=str)
        return yaml.dump(_blockify(args), default_flow_style=False, allow_unicode=True, width=120)

    if fmt == "compact":
        return json.dumps(args, indent=2, default=str)

    if fmt == "text":
        return str(args)

    # json (default)
    return json.dumps(args, ensure_ascii=False, default=str)
