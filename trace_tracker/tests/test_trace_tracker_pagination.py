from __future__ import annotations

import json
import unittest
from typing import Any

from trace_tracker.core import render
from trace_tracker.core.adapter import (
    Adapter,
    AdapterRegistry,
    MultiRootAdapter,
)
from trace_tracker.core.models import AdapterStatus, SessionSummary
from trace_tracker.core.server import _Handler


class _RecordingAdapter(Adapter):
    name = "codex"
    label = "Codex"

    def __init__(
        self,
        *,
        root_label: str,
        source: str,
        session_ids: list[str],
    ) -> None:
        self.root_label = root_label
        self.root_source = source
        self.session_ids = session_ids
        self.list_limits: list[int] = []

    def status(self) -> AdapterStatus:
        return AdapterStatus(available=True, note="ready", data_path=self.root_label)

    def list_sessions(self, limit: int) -> list[SessionSummary]:
        self.list_limits.append(limit)
        return [
            SessionSummary(
                agent=self.name,
                session_id=session_id,
                started_at=float(len(self.session_ids) - index),
            )
            for index, session_id in enumerate(self.session_ids[:limit])
        ]


class TraceTrackerPaginationTests(unittest.TestCase):
    def test_multi_root_groups_keep_independent_root_keys(self) -> None:
        first = _RecordingAdapter(
            root_label="/traces/first", source="user", session_ids=["first-1"]
        )
        second = _RecordingAdapter(
            root_label="/traces/second", source="traces", session_ids=["second-1"]
        )
        adapter = MultiRootAdapter([first, second])

        groups = adapter.session_groups("all")

        self.assertEqual([group.key for group in groups], ["0", "1"])
        self.assertEqual(
            [(group.source, group.label) for group in groups],
            [("user", "/traces/first"), ("traces", "/traces/second")],
        )

    def test_multi_root_page_only_reads_requested_root(self) -> None:
        first = _RecordingAdapter(
            root_label="/traces/first",
            source="user",
            session_ids=["first-1", "first-2", "first-3"],
        )
        second = _RecordingAdapter(
            root_label="/traces/second",
            source="traces",
            session_ids=["second-1", "second-2", "second-3"],
        )
        adapter = MultiRootAdapter([first, second])

        page = adapter.list_session_page(group_key="1", limit=2, offset=0)

        self.assertEqual(first.list_limits, [])
        self.assertEqual(second.list_limits, [3])
        self.assertEqual(
            [session.session_id for session in page.sessions],
            ["second-1", "second-2"],
        )
        self.assertEqual(page.next_offset, 2)
        self.assertEqual(page.sessions[0].extras["trace_root"], "/traces/second")
        self.assertEqual(page.sessions[0].extras["trace_source"], "traces")

    def test_list_page_renders_lazy_root_shell_without_reading_sessions(self) -> None:
        adapter = _RecordingAdapter(
            root_label="/traces/codex", source="user", session_ids=["session-1"]
        )
        registry = AdapterRegistry([adapter])
        handler = object.__new__(_Handler)
        handler.registry = registry
        handler.default_limit = 50
        sent: list[str] = []
        handler._send = lambda body, **_: sent.append(str(body))  # type: ignore[method-assign]

        handler._handle_list(adapter, {})

        self.assertEqual(adapter.list_limits, [])
        self.assertEqual(len(sent), 1)
        self.assertIn('data-root-key="0"', sent[0])
        self.assertIn('data-page-size="50"', sent[0])
        self.assertIn("Scroll to load sessions", sent[0])

    def test_sessions_json_returns_one_page_and_next_offset(self) -> None:
        adapter = _RecordingAdapter(
            root_label="/traces/codex",
            source="user",
            session_ids=["session-1", "session-2", "session-3"],
        )
        registry = AdapterRegistry([adapter])
        handler = object.__new__(_Handler)
        handler.registry = registry
        handler.default_limit = 50
        sent: list[tuple[str, dict[str, Any]]] = []
        handler._send = (  # type: ignore[method-assign]
            lambda body, **kwargs: sent.append((str(body), kwargs))
        )

        handler._handle_list_json(
            adapter,
            {"root": ["0"], "limit": ["2"], "offset": ["0"], "source": ["user"]},
        )

        payload = json.loads(sent[0][0])
        self.assertEqual(adapter.list_limits, [3])
        self.assertEqual(
            [session["session_id"] for session in payload["sessions"]],
            ["session-1", "session-2"],
        )
        self.assertEqual(payload["next_offset"], 2)
        self.assertTrue(payload["has_more"])
        self.assertEqual(payload["session_group_count"], 2)
        self.assertEqual(payload["thread_count"], 2)
        self.assertIn("rows_html", payload)

    def test_rendered_lazy_page_keeps_manual_load_fallback(self) -> None:
        adapter = _RecordingAdapter(
            root_label="/traces/codex", source="user", session_ids=[]
        )
        registry = AdapterRegistry([adapter])

        html = render.render_list_page(
            registry=registry,
            adapter=adapter,
            sessions=[],
            limit=50,
            selected_source="all",
            groups=adapter.session_groups("all"),
        )

        self.assertIn("Load 50 more", html)
        self.assertIn('aria-live="polite"', html)
        self.assertIn('src="/static/list.js"', html)

    def test_rendered_rows_group_main_and_subagent_threads(self) -> None:
        parent_id = "019f49be-1856-7d32-9442-79dca10240aa"
        main = SessionSummary(
            agent="codex",
            session_id=f"rollout-main-{parent_id}",
            title="Main analysis",
            started_at=10.0,
            extras={
                "group_session_id": parent_id,
                "thread_id": parent_id,
                "thread_kind": "main",
                "thread_depth": 0,
                "trace_source": "user",
                "trace_root": "/traces/codex",
            },
        )
        subagent = SessionSummary(
            agent="codex",
            session_id="rollout-subagent-thread",
            title="Review online logic",
            started_at=11.0,
            extras={
                "group_session_id": parent_id,
                "thread_id": "019f49c3-32e0-7a83-a2bb-4131ffc67ff2",
                "parent_thread_id": parent_id,
                "thread_kind": "subagent",
                "thread_depth": 1,
                "agent_label": "Singer",
                "agent_path": "/root/review_online_react",
                "trace_source": "user",
                "trace_root": "/traces/codex",
            },
        )

        html = render.render_session_rows([subagent, main], show_profile=False)

        self.assertIn('class="session-group-id">Session', html)
        self.assertIn('title="019f49be-1856-7d32-9442-79dca10240aa"', html)
        self.assertIn("1 main · 1 subagent", html)
        self.assertIn("MAIN", html)
        self.assertIn("SUB · d1", html)
        self.assertIn("Singer", html)
        self.assertIn("review_online_react", html)
        self.assertIn('class="session-group-toggle"', html)


if __name__ == "__main__":
    unittest.main()
