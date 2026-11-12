from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from typing import Any

import zstandard

from trace_tracker.adapters.dsh.live import LiveTraceReader
from trace_tracker.adapters.dsh.parser import parse, session_header


class DSHParserTests(unittest.TestCase):
    def test_plain_and_appended_compressed_frames_preserve_tools(self) -> None:
        records = [
            {"type": "session", "version": 4, "id": "example"},
            {"type": "assistant/message", "time": 1785405603000, "data": {
                "message": {"content": [
                    {"type": "reasoning", "text": "Inspect the project."},
                    {"type": "tool-call", "id": "call-1", "name": "bash",
                     "arguments": '{"command":"pwd"}'},
                ]}}},
            {"type": "tool/result", "time": 1785405604000, "data": {
                "message": {"source": {"callId": "call-1"},
                            "content": [{"type": "text", "text": "/tmp/project"}]}}},
        ]
        lines = [(json.dumps(record) + "\n").encode() for record in records]
        with tempfile.TemporaryDirectory() as tmp:
            plain = Path(tmp) / "session.jsonl"
            plain.write_bytes(b"".join(lines))
            expected = parse(plain)
            self.assertIsNotNone(expected)
            assert expected is not None
            self.assertEqual(expected.messages[0].reasoning, "Inspect the project.")
            self.assertEqual(expected.messages[0].tool_calls[0].name, "bash")
            self.assertEqual(expected.messages[1].tool_call_id, "call-1")

            compressed = Path(tmp) / "session.jsonl.zstd"
            frames = [zstandard.ZstdCompressor().compress(line) for line in lines]
            compressed.write_bytes(b"".join(frames[:2]) + frames[2][:10])
            growing = parse(compressed)
            self.assertIsNotNone(growing)
            assert growing is not None
            self.assertEqual(len(growing.messages), 1)
            with compressed.open("ab") as handle:
                handle.write(frames[2][10:])
            completed = parse(compressed)
            self.assertIsNotNone(completed)
            assert completed is not None
            self.assertEqual(completed.messages, expected.messages)
            self.assertEqual(session_header(compressed)["id"], "example")

    def test_rpc_events_select_one_session(self) -> None:
        records = [
            {"method": "session.event", "params": {"sessionId": session_id,
                "event": {"type": "user/message", "time": 1785405603000,
                          "data": {"content": [{"type": "text", "text": text}]}}}}
            for session_id, text in (("parent", "Main task"), ("child", "Child task"))
        ]
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "events.jsonl"
            path.write_text("".join(json.dumps(record) + "\n" for record in records))
            detail = parse(path, session_id="child")
            self.assertIsNotNone(detail)
            assert detail is not None
            self.assertEqual([message.content for message in detail.messages], ["Child task"])

    def test_live_reader_retries_partial_lines_and_separates_sessions(self) -> None:
        def frame(session_id: str, value: dict[str, Any]) -> str:
            return json.dumps({"method": "session.stream", "params": {
                "sessionId": session_id, "time": 1785405603000,
                "frame": {"attemptId": "a1", **value}}}) + "\n"

        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "stream.jsonl"
            path.write_text(frame("parent", {"type": "start", "turn": 1, "step": 1})
                            + frame("child", {"type": "start", "turn": 1, "step": 1})
                            + frame("child", {"type": "chunk", "chunk": {
                                "type": "text-delta", "index": 0, "text": "Child reply"}}))
            reader = LiveTraceReader(path)
            tail = frame("parent", {"type": "chunk", "chunk": {
                "type": "reasoning-delta", "index": 0, "text": "Check files"}})
            with path.open("a") as handle:
                handle.write(tail[:20])
            partial = reader.message("parent")
            self.assertIsNotNone(partial)
            assert partial is not None
            self.assertEqual(partial.reasoning, "")
            with path.open("a") as handle:
                handle.write(tail[20:])
            complete = reader.message("parent")
            child = reader.message("child")
            self.assertIsNotNone(complete)
            self.assertIsNotNone(child)
            assert complete is not None and child is not None
            self.assertEqual(complete.reasoning, "Check files")
            self.assertEqual(complete.content, "")
            self.assertEqual(child.content, "Child reply")
