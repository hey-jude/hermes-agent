"""tool-output-format plugin — YAML display formatting for tool output.

Wires one behaviour:

* ``format_tool_result_for_display`` hook — reformats tool output for
  user-facing display (CLI preview, gateway verbose args) without
  affecting what the LLM sees.

Supported formats: json (default), yaml, text, compact.

Configuration in config.yaml::

    tool_output:
      format: yaml  # json | yaml | text | compact

The plugin reads the format setting and applies it at display time only.
LLM conversation history is never modified.
"""

from __future__ import annotations

import json
import logging
import re
from typing import Any, Optional

from tools.tool_output_limits import (
    format_tool_result_for_display as _format_result,
    format_args_for_display as _format_args,
    get_tool_output_format,
)

logger = logging.getLogger(__name__)


_ESCAPE_MAP = {"n": "\n", "t": "\t"}


def _normalize_newlines(text: str) -> str:
    """Convert literal ``\\n`` / ``\\t`` escape sequences to real characters.

    Terminal output sometimes contains escaped newlines (backslash + n)
    instead of actual newline characters — e.g. when a command echoes
    ``'line1\\nline2'``.  This helper makes them render correctly in
    the CLI display without touching the LLM-facing content.

    Handles ``\\\\n`` (escaped-backslash + n) correctly: the ``\\\\``
    becomes a single ``\\`` and the trailing ``n`` is preserved literally.
    """
    def _replace_escape(m: re.Match) -> str:
        bs, letter = m.group(1), m.group(2)
        n_bs = len(bs)
        if n_bs % 2 == 1:
            # Odd backslashes: last one is part of the escape sequence.
            # Pairs before it are literal backslashes.
            prefix = "\\" * (n_bs // 2)
            replacement = _ESCAPE_MAP.get(letter)
            return prefix + (replacement if replacement is not None else letter)
        else:
            # Even backslashes: all are literal, none part of an escape.
            return "\\" * (n_bs // 2) + letter

    return re.sub(r"(\\+)([a-zA-Z])", _replace_escape, text)


# Tools whose argument values should be wrapped in a fenced code block
# for gateway progress display (Discord/Telegram markdown).  Mirrors how
# the gateway already wraps a ``terminal`` command in a bare fence.
#
# Boxed fields are emitted as a fenced block per field so multi-line content
# (write_file content, execute_code code, patch old/new_string) renders in
# monospace instead of a truncated one-line preview.
BOXED_TOOLS = {
    "write_file": ["content"],
    "execute_code": ["code"],
    "patch": ["old_string", "new_string", "patch"],
}


def _boxed_fence(tool_name: str, args: Any) -> Optional[str]:
    """Return a fenced-block string for boxed tools, or None.

    Used by the ``gateway_progress`` display context so Discord/Telegram
    render multi-line tool args in a markdown code block.  Boxed fields
    (write_file ``content``, execute_code ``code``, patch ``old_string``/
    ``new_string``/``patch``) each become their own fenced block.  Remaining
    args
    (path, mode, replace_all, ...) are appended ABOVE the fenced block as
    single-line summary lines so the user sees where/what the tool targets
    before the multi-line payload.  Non-boxed tools or non-dict args return
    None (caller keeps its normal progress flow).
    """
    fields = BOXED_TOOLS.get(tool_name)
    if not fields or not isinstance(args, dict):
        return None
    parts: list[str] = []
    for field in fields:
        val = args.get(field)
        if not isinstance(val, str):
            continue
        body = val.rstrip("\n")
        if not body:
            continue
        parts.append(f"{field}:")
        parts.append("```")
        parts.append(body)
        parts.append("```")
    # Non-fenced args (path, mode, replace_all, ...) were previously
    # dropped entirely from the progress display.  Surface them above the
    # fenced block so the user sees the full tool call, not just the
    # multi-line payload.  Only emit when at least one boxed field was
    # actually rendered — otherwise (e.g. a malformed call where every
    # boxed field is empty) we'd collapse a huge value into a single
    # inline-code line and produce an unreadable progress message.
    extra: list[str] = []
    for key, val in args.items():
        if key in fields or val is None:
            continue
        if isinstance(val, str):
            # Collapse to a single line, wrap in backticks so path-like
            # values render as inline code in Discord/Telegram.  Escape
            # embedded backticks by widening the fence (``...``) so a path
            # containing a backtick can't break markdown rendering.
            shown = " ".join(val.split())
            wrapped = f"``{shown}``" if "`" in shown else f"`{shown}`"
            extra.append(f"{key}: {wrapped}")
        else:
            extra.append(f"{key}: {val}")
    if extra and parts:
        parts = ["\n".join(extra)] + parts
    return "\n".join(parts) if parts else None


def _extract_display_payload(result: Any, tool_name: str) -> Any:
    """Extract the display-relevant part of a tool result.

    Terminal tool returns ``{"output": ..., "exit_code": ..., "error": ...}``
    but users only care about the ``output`` field for display.  Other tools
    return heterogeneous JSON — pass them through unchanged.
    """
    if tool_name == "terminal" and isinstance(result, str):
        try:
            parsed = json.loads(result)
            if isinstance(parsed, dict) and "output" in parsed:
                return _normalize_newlines(parsed["output"])
        except (json.JSONDecodeError, TypeError, KeyError):
            pass
    return result


def _on_format_tool_result_for_display(
    *,
    tool_name: str = "",
    args: Any = None,
    result: Any = None,
    display_context: str = "",
    **kwargs: Any,
) -> Optional[str]:
    """Format tool output for display only. Does NOT affect LLM input."""
    fmt = get_tool_output_format()

    # Default format (json) — don't transform
    if fmt == "json":
        return None

    logger.debug(
        "format_tool_result_for_display: tool=%s context=%s fmt=%s",
        tool_name,
        display_context or "cli",
        fmt,
    )

    # For gateway progress display, return the boxed tool's args as a fenced
    # block (without a header — the gateway adds the "{emoji} {tool_name}"
    # label, mirroring the terminal command path).  Returns the fenced string
    # (caller prepends the tool label and sends it) or None so non-boxed tools
    # keep their default short preview.
    if display_context == "gateway_progress":
        fenced = _boxed_fence(tool_name, args)
        if fenced is not None:
            logger.debug(
                "format_tool_result_for_display: boxed fence for %s (%d chars)",
                tool_name,
                len(fenced),
            )
            return fenced
        return None

    # For gateway verbose args, format the args dict
    if display_context == "gateway_verbose_args" and args is not None:
        formatted = _format_args(args, fmt=fmt)
        logger.debug(
            "format_tool_result_for_display: formatted args for %s (%d chars)",
            tool_name,
            len(formatted),
        )
        return formatted

    # For CLI display, extract terminal output field then format
    if result is not None:
        payload = _extract_display_payload(result, tool_name)
        formatted = _format_result(payload, fmt=fmt)
        logger.debug(
            "format_tool_result_for_display: formatted result for %s (%d chars)",
            tool_name,
            len(formatted),
        )
        return formatted

    return None


def register(ctx: Any) -> None:
    """Register the plugin with the PluginContext."""
    ctx.register_hook("format_tool_result_for_display", _on_format_tool_result_for_display)
    fmt = get_tool_output_format()
    logger.info(
        "tool-output-format plugin registered (format=%s)",
        fmt,
    )
