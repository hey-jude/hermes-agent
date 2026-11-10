"""Boxed tool args (write_file/execute_code/patch) render as fenced progress blocks.

A one-line preview of ``write_file`` hides what is about to be written, and the
progress queue is the only place a chat user sees the call. These tests pin the
contract: markdown platforms get the fenced payload (so a multi-line content args
stays readable), non-markdown platforms keep the plain preview, and a following
terminal block still gets its header.
"""

from __future__ import annotations

import queue
from types import SimpleNamespace

from gateway.run_turn_progress_boxed import boxed_tool_block


class TestBoxedToolBlock:
    def test_write_file_payload_is_fenced_with_path_above_it(self):
        block = boxed_tool_block(
            "write_file", {"path": "a.py", "content": "x = 1\ny = 2"}, full=True, cap=40,
        )

        assert block is not None
        assert "path: `a.py`" in block
        assert "content:\n```\nx = 1\ny = 2\n```" in block

    def test_execute_code_payload_is_fenced(self):
        block = boxed_tool_block("execute_code", {"code": "print(1)\n"}, full=True, cap=40)

        assert block == "code:\n```\nprint(1)\n```"

    def test_patch_renders_both_sides_in_field_order(self):
        block = boxed_tool_block(
            "patch",
            {"path": "a.py", "old_string": "old", "new_string": "new"},
            full=True,
            cap=40,
        )

        assert block is not None
        assert block.index("old_string:") < block.index("new_string:")
        assert "```\nold\n```" in block
        assert "```\nnew\n```" in block

    def test_non_boxed_tool_declines(self):
        assert boxed_tool_block("read_file", {"path": "a.py"}, full=True, cap=40) is None

    def test_non_dict_args_declines(self):
        assert boxed_tool_block("write_file", "not-a-dict", full=True, cap=40) is None

    def test_payloadless_call_declines_so_the_preview_survives(self):
        """A call whose boxed fields are all empty keeps the plain preview."""
        assert boxed_tool_block("write_file", {"path": "a.py", "content": ""}, full=True, cap=40) is None

    def test_short_mode_caps_the_body_and_reports_hidden_lines(self):
        body = "\n".join(f"line{i}" for i in range(20))
        block = boxed_tool_block("write_file", {"path": "a.py", "content": body}, full=False, cap=20)

        assert block is not None
        assert " ..." in block
        assert "(+" in block and "lines)" in block
        assert "line19" not in block

    def test_verbose_mode_shows_the_whole_body(self):
        body = "\n".join(f"line{i}" for i in range(20))
        block = boxed_tool_block("write_file", {"path": "a.py", "content": body}, full=True, cap=20)

        assert block is not None
        assert "line19" in block
        assert " ..." not in block

    def test_payload_holding_a_fence_widens_the_fence(self):
        block = boxed_tool_block(
            "write_file", {"path": "a.md", "content": "```bash\nls\n```"}, full=True, cap=40,
        )

        assert block is not None
        assert "````\n```bash\nls\n```\n````" in block

    def test_payload_holding_both_fence_lengths_declines(self):
        assert boxed_tool_block(
            "write_file", {"path": "a.md", "content": "```\n````\n"}, full=True, cap=40,
        ) is None


class TestProgressMessageIntegration:
    def _runner(self, supports_code_blocks: bool, *, progress_mode: str = "all"):
        from gateway.run_turn_runner import TurnRunner

        adapter = SimpleNamespace(
            supports_code_blocks=supports_code_blocks,
            format_tool_preview=lambda prepared: prepared.text,
        )
        ctx = SimpleNamespace(
            source=None,
            progress_mode=progress_mode,
            last_was_terminal_block=[True],
            progress_queue=queue.Queue(),
        )
        runner = SimpleNamespace(_delivery_adapter_for=lambda source: adapter)
        return TurnRunner(runner, ctx), ctx

    def test_markdown_platform_emits_the_fenced_payload(self):
        turn, ctx = self._runner(True)

        message = turn._progress_build_message("write_file", "a.py", {"path": "a.py", "content": "x = 1\ny = 2"})

        assert message is not None
        assert "```" in message and "y = 2" in message
        # The following terminal block owes its header again.
        assert ctx.last_was_terminal_block[0] is False

    def test_non_markdown_platform_keeps_the_preview(self):
        turn, _ctx = self._runner(False)

        message = turn._progress_build_message("write_file", "a.py", {"path": "a.py", "content": "x = 1\ny = 2"})

        assert message is not None
        assert "```" not in message
        assert "a.py" in message

    def test_verbose_mode_queues_the_block_directly(self):
        turn, ctx = self._runner(True, progress_mode="verbose")

        assert turn._progress_build_message("write_file", "a.py", {"path": "a.py", "content": "x = 1"}) is None
        assert "```" in ctx.progress_queue.get_nowait()
