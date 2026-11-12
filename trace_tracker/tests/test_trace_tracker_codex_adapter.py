from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path

from trace_tracker.adapters.codex.adapter import CodexAdapter


class TraceTrackerCodexAdapterTests(unittest.TestCase):
    def test_session_page_keeps_main_and_subagent_in_same_group(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / ".codex"
            sessions_dir = root / "sessions" / "2026" / "07" / "10"
            parent_id = "019f49be-1856-7d32-9442-79dca10240aa"
            child_id = "019f49c3-32e0-7a83-a2bb-4131ffc67ff2"
            older_id = "019f4911-32e0-7a83-a2bb-4131ffc67ff2"
            older_path = sessions_dir / f"rollout-2026-07-09T10-00-00-{older_id}.jsonl"
            main_path = sessions_dir / f"rollout-2026-07-10T10-00-00-{parent_id}.jsonl"
            child_path = sessions_dir / f"rollout-2026-07-10T10-02-37-{child_id}.jsonl"
            self.write_session(older_path, session_id=older_id, thread_id=older_id)
            self.write_session(main_path, session_id=parent_id, thread_id=parent_id)
            self.write_session(
                child_path,
                session_id=parent_id,
                thread_id=child_id,
                parent_thread_id=parent_id,
            )
            os.utime(older_path, (1.0, 1.0))
            os.utime(main_path, (2.0, 2.0))
            os.utime(child_path, (3.0, 3.0))

            adapter = CodexAdapter(root=root)
            page = adapter.list_session_page(group_key="0", limit=1, offset=0)

            self.assertEqual(page.session_group_count, 1)
            self.assertEqual(page.next_offset, 1)
            self.assertEqual(
                {session.session_id for session in page.sessions},
                {main_path.stem, child_path.stem},
            )
            self.assertEqual(
                {session.extras["group_session_id"] for session in page.sessions},
                {parent_id},
            )

    def write_session(
        self,
        path: Path,
        *,
        session_id: str,
        thread_id: str,
        parent_thread_id: str | None = None,
    ) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "session_id": session_id,
            "id": thread_id,
            "thread_source": "subagent" if parent_thread_id else "user",
        }
        if parent_thread_id:
            payload["parent_thread_id"] = parent_thread_id
        path.write_text(
            json.dumps(
                {
                    "type": "session_meta",
                    "timestamp": "2026-07-10T02:02:37Z",
                    "payload": payload,
                }
            )
            + "\n",
            encoding="utf-8",
        )


if __name__ == "__main__":
    unittest.main()
