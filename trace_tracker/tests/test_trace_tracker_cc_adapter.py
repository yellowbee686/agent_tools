from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path
from typing import Any

from trace_tracker.adapters.cc.adapter import CCAdapter


class TraceTrackerCCAdapterTests(unittest.TestCase):
    def test_loads_claude_code_subagent_sessions_by_agent_id(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / ".claude"
            project = root / "projects" / "-Users-example-project"
            parent_id = "78efa99c-a4c3-4976-a3ca-8b1dbfa60cbd"
            agent_session_id = "agent-a725cf31cf0ef440a"

            self.write_cc_session(project / f"{parent_id}.jsonl", parent_id)
            self.write_cc_session(
                project
                / parent_id
                / "subagents"
                / "workflows"
                / "wf_f158084d-abd"
                / f"{agent_session_id}.jsonl",
                parent_id,
                agent_id="a725cf31cf0ef440a",
            )

            adapter = CCAdapter(root=root)

            self.assertTrue(adapter.is_valid_session_id(agent_session_id))
            loaded = adapter.load_session(agent_session_id)

            self.assertIsNotNone(loaded)
            assert loaded is not None
            self.assertEqual(loaded.summary.session_id, agent_session_id)
            self.assertEqual(loaded.summary.extras["group_session_id"], parent_id)
            self.assertEqual(loaded.summary.extras["thread_id"], agent_session_id)
            self.assertEqual(loaded.summary.extras["thread_kind"], "subagent")
            self.assertEqual(loaded.summary.extras["parent_thread_id"], parent_id)
            self.assertEqual(loaded.summary.extras["thread_depth"], 1)

    def test_session_page_keeps_main_and_subagent_in_same_group(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / ".claude"
            project = root / "projects" / "-Users-example-project"
            grouped_session_id = "78efa99c-a4c3-4976-a3ca-8b1dbfa60cbd"
            older_session_id = "89fab00d-b5d4-4087-b4db-9c2ec0b71dce"
            agent_session_id = "agent-a725cf31cf0ef440a"
            main_path = project / f"{grouped_session_id}.jsonl"
            subagent_path = (
                project
                / grouped_session_id
                / "subagents"
                / f"{agent_session_id}.jsonl"
            )
            older_path = project / f"{older_session_id}.jsonl"
            self.write_cc_session(older_path, older_session_id)
            self.write_cc_session(main_path, grouped_session_id)
            self.write_cc_session(
                subagent_path,
                grouped_session_id,
                agent_id="a725cf31cf0ef440a",
            )
            os.utime(older_path, (1, 1))
            os.utime(main_path, (2, 2))
            os.utime(subagent_path, (3, 3))

            adapter = CCAdapter(root=root)
            page = adapter.list_session_page(group_key="0", limit=1, offset=0)

            self.assertEqual(page.session_group_count, 1)
            self.assertEqual(page.next_offset, 1)
            self.assertEqual(
                {session.session_id for session in page.sessions},
                {grouped_session_id, agent_session_id},
            )
            self.assertEqual(
                {session.extras["group_session_id"] for session in page.sessions},
                {grouped_session_id},
            )

    def test_loads_reasoning_and_request_context_from_native_trace(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / ".claude"
            project = root / "projects" / "-Users-example-project"
            session_id = "78efa99c-a4c3-4976-a3ca-8b1dbfa60cbd"
            trace_path = project / f"{session_id}.jsonl"
            self.write_cc_session(
                trace_path,
                session_id,
                reasoning="Inspect the trace first.",
            )
            trace_path.with_suffix(".context.json").write_text(
                json.dumps(
                    {
                        "system_prompt": "You are Claude Code.",
                        "tools": [
                            {
                                "name": "Read",
                                "description": "Read a file",
                                "input_schema": {"type": "object"},
                            }
                        ],
                    }
                ),
                encoding="utf-8",
            )

            adapter = CCAdapter(root=root)
            loaded = adapter.load_session(session_id)

            self.assertIsNotNone(loaded)
            assert loaded is not None
            self.assertEqual(loaded.system_prompt, "You are Claude Code.")
            self.assertEqual(
                loaded.messages[1].reasoning,
                "Inspect the trace first.",
            )
            self.assertEqual(
                loaded.tools_def[0]["function"]["name"],
                "Read",
            )

    def write_cc_session(
        self,
        path: Path,
        session_id: str,
        agent_id: str | None = None,
        reasoning: str | None = None,
    ) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        user_record: dict[str, Any] = {
            "type": "user",
            "timestamp": "2026-06-09T16:36:44.926Z",
            "sessionId": session_id,
            "cwd": "/Users/example/project",
            "message": {"role": "user", "content": "hello"},
        }
        if agent_id:
            user_record["agentId"] = agent_id
            user_record["isSidechain"] = True
            user_record["slug"] = "resilient-willow"

        assistant_content: list[dict[str, str]] = []
        if reasoning:
            assistant_content.append(
                {"type": "thinking", "thinking": reasoning}
            )
        assistant_content.append({"type": "text", "text": "done"})
        assistant_record = {
            "type": "assistant",
            "timestamp": "2026-06-09T16:36:54.417Z",
            "message": {
                "role": "assistant",
                "model": "claude-test",
                "content": assistant_content,
                "usage": {"input_tokens": 1, "output_tokens": 1},
            },
        }

        path.write_text(
            "\n".join(
                [
                    json.dumps(user_record, ensure_ascii=False),
                    json.dumps(assistant_record, ensure_ascii=False),
                ]
            )
            + "\n",
            encoding="utf-8",
        )


if __name__ == "__main__":
    unittest.main()
