"""Boxed tool args as fenced blocks in gateway progress.

``GatewayTurnProgressMixin._progress_terminal_blocks`` fences a ``terminal``
command. The multi-line payload tools — ``write_file`` (``content``),
``execute_code`` (``code``) and ``patch`` (``old_string``/``new_string`` plus the
V4A ``patch`` field) — only ever reach the chat as a one-line preview, so the
user cannot see what is about to be written. This renders those payloads as a
fenced block on markdown platforms, with the scalar args (``path``, ``mode``,
...) on a summary line above it.

Verbose shows the whole payload; ``all``/``new`` cap it at
``tool_preview_length`` and report the remaining line count, mirroring the short
terminal command. Fences widen to four backticks when the payload contains a
bare ``` (Discord/Telegram end a block at any ``` inside it); a payload holding
both falls back to the caller's preview rather than emitting a broken block.
"""

from __future__ import annotations

from typing import Any, Optional

# Field order is the render order inside the block.
BOXED_FIELDS: dict[str, tuple[str, ...]] = {
    "write_file": ("content",),
    "execute_code": ("code",),
    "patch": ("old_string", "new_string", "patch"),
}


def _fence_for(body: str) -> Optional[str]:
    """Fence that survives ``body``, or None when no fence length is safe."""
    if "```" not in body:
        return "```"
    if "````" not in body:
        return "````"
    return None


def _summary(args: dict[str, Any], fields: tuple[str, ...], cap: int) -> str:
    """One line of scalar args (path, mode, ...): where/what the payload targets."""
    parts: list[str] = []
    for key, value in args.items():
        if key in fields or value is None:
            continue
        if isinstance(value, str):
            shown = " ".join(value.split())
            if not shown:
                continue
            # Widen the inline span so a path holding a backtick cannot close it early.
            tick = "``" if "`" in shown else "`"
            parts.append(f"{key}: {tick}{shown}{tick}")
        else:
            parts.append(f"{key}: {value}")
    line = " · ".join(parts)
    return line if len(line) <= cap else line[: cap - 3] + "..."


def boxed_tool_block(tool_name: str, args: Any, *, full: bool, cap: int) -> Optional[str]:
    """Fenced block for a boxed tool's payload, or None so the caller keeps its preview.

    ``full`` (verbose) renders the whole payload; otherwise each body is capped
    at ``cap`` characters and the hidden remainder is reported as a line count.
    """
    fields = BOXED_FIELDS.get(tool_name)
    if not fields or not isinstance(args, dict):
        return None
    blocks: list[str] = []
    summary = _summary(args, fields, cap)
    if summary:
        blocks.append(summary)
    for field in fields:
        body = args.get(field)
        if not isinstance(body, str):
            continue
        body = body.rstrip("\n")
        if not body.strip():
            continue
        fence = _fence_for(body)
        if fence is None:
            continue
        if not full and len(body) > cap:
            shown = body[:cap].rstrip()
            hidden = (body.count("\n") + 1) - (shown.count("\n") + 1)
            body = f"{shown} ..." + (f"\n(+{hidden} lines)" if hidden > 0 else "")
        blocks.append(f"{field}:")
        blocks.append(f"{fence}\n{body}\n{fence}")
    # No boxed field rendered (a malformed call): the plain preview is better than
    # a lone summary line, so decline.
    return "\n".join(blocks) if len(blocks) > (1 if summary else 0) else None
