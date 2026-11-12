from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from typing import Any

from trace_tracker.adapters.codex.parser import parse


class CodexParserTests(unittest.TestCase):
    def test_code_mode_custom_calls_and_outputs_are_visible(self) -> None:
        source = 'text(await tools.exec_command({cmd: "pwd"}));'
        with tempfile.TemporaryDirectory() as tmp:
            session_path = Path(tmp) / "rollout-code-mode.jsonl"
            self.write_jsonl(session_path, [
                self.session_meta(),
                {"type": "response_item", "payload": {
                    "type": "custom_tool_call", "call_id": "code-1", "name": "exec", "namespace": "functions", "input": source,
                }},
                {"type": "response_item", "payload": {
                    "type": "custom_tool_call_output", "call_id": "code-1", "output": [
                        {"type": "input_text", "text": "Script completed"},
                        {"type": "input_text", "text": "/project"},
                    ],
                }},
            ])
            detail = parse(session_path)
        self.assertIsNotNone(detail)
        self.assertEqual(detail.messages[0].tool_calls[0].name, "functions.exec")
        self.assertEqual(detail.messages[0].tool_calls[0].arguments, source)
        self.assertEqual(detail.messages[1].role, "tool")
        self.assertEqual(detail.messages[1].tool_call_id, "code-1")
        self.assertEqual(detail.messages[1].content, "Script completed\n/project")

    def test_subagent_uses_matching_thread_metadata_for_session_group(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            parent_id = "019f49be-1856-7d32-9442-79dca10240aa"
            thread_id = "019f49c3-32e0-7a83-a2bb-4131ffc67ff2"
            session_path = (
                Path(tmp) / f"rollout-2026-07-10T10-02-37-{thread_id}.jsonl"
            )
            self.write_jsonl(
                session_path,
                [
                    {
                        "type": "session_meta",
                        "timestamp": "2026-07-10T02:02:37Z",
                        "payload": {
                            "session_id": parent_id,
                            "id": thread_id,
                            "parent_thread_id": parent_id,
                            "thread_source": "subagent",
                            "agent_nickname": "Singer",
                            "agent_path": "/root/review_online_react",
                            "source": {
                                "subagent": {
                                    "thread_spawn": {
                                        "parent_thread_id": parent_id,
                                        "depth": 1,
                                    }
                                }
                            },
                        },
                    },
                    {
                        "type": "session_meta",
                        "timestamp": "2026-07-10T02:02:38Z",
                        "payload": {
                            "session_id": parent_id,
                            "id": parent_id,
                            "thread_source": "user",
                            "cwd": "/wrong-parent-context",
                        },
                    },
                ],
            )

            detail = parse(session_path)

            self.assertIsNotNone(detail)
            assert detail is not None
            self.assertEqual(detail.summary.extras["group_session_id"], parent_id)
            self.assertEqual(detail.summary.extras["thread_id"], thread_id)
            self.assertEqual(detail.summary.extras["thread_kind"], "subagent")
            self.assertEqual(detail.summary.extras["parent_thread_id"], parent_id)
            self.assertEqual(detail.summary.extras["thread_depth"], 1)
            self.assertEqual(detail.summary.extras["agent_label"], "Singer")
            self.assertEqual(
                detail.summary.extras["agent_path"], "/root/review_online_react"
            )

    def test_reasoning_text_content_is_displayed(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            session_path = Path(tmp) / "rollout-test.jsonl"
            self.write_jsonl(
                session_path,
                [
                    self.session_meta(),
                    {
                        "type": "response_item",
                        "timestamp": "2026-06-04T10:00:01Z",
                        "payload": {
                            "type": "reasoning",
                            "summary": [],
                            "content": [
                                {
                                    "type": "reasoning_text",
                                    "text": "visible reasoning",
                                }
                            ],
                        },
                    },
                    {
                        "type": "event_msg",
                        "timestamp": "2026-06-04T10:00:02Z",
                        "payload": {
                            "type": "token_count",
                            "info": {
                                "total_token_usage": {
                                    "input_tokens": 1,
                                    "output_tokens": 2,
                                    "reasoning_output_tokens": 0,
                                }
                            },
                        },
                    },
                ],
            )

            detail = parse(session_path)

            self.assertIsNotNone(detail)
            assert detail is not None
            self.assertEqual(len(detail.messages), 1)
            self.assertEqual(detail.messages[0].role, "reasoning")
            self.assertEqual(detail.messages[0].reasoning, "visible reasoning")
            self.assertEqual(detail.summary.extras["reasoning_output_tokens"], 0)

    def test_reasoning_summary_still_displays_when_content_is_absent(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            session_path = Path(tmp) / "rollout-test.jsonl"
            self.write_jsonl(
                session_path,
                [
                    self.session_meta(),
                    {
                        "type": "response_item",
                        "timestamp": "2026-06-04T10:00:01Z",
                        "payload": {
                            "type": "reasoning",
                            "summary": [
                                {
                                    "type": "summary_text",
                                    "text": "summary reasoning",
                                }
                            ],
                        },
                    },
                ],
            )

            detail = parse(session_path)

            self.assertIsNotNone(detail)
            assert detail is not None
            self.assertEqual(detail.messages[0].reasoning, "summary reasoning")

    def session_meta(self) -> dict[str, Any]:
        return {
            "type": "session_meta",
            "timestamp": "2026-06-04T10:00:00Z",
            "payload": {"timestamp": "2026-06-04T10:00:00Z", "cwd": "/tmp"},
        }

    def write_jsonl(self, path: Path, records: list[dict[str, Any]]) -> None:
        text = "\n".join(json.dumps(record) for record in records) + "\n"
        path.write_text(text, encoding="utf-8")


if __name__ == "__main__":
    unittest.main()
