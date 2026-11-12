"""Unified data models shared across all agent adapters.

Each adapter must produce these structures from its own underlying data
(SQLite, JSONL, etc.) so the renderer can stay generic.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class ToolCall:
    """A single tool invocation produced by an assistant message."""

    id: str = ""
    name: str = ""
    arguments: Any = ""  # str (raw JSON) or dict / list

    def pretty_arguments(self) -> str:
        """Best-effort pretty JSON; fall back to str."""
        import json

        v = self.arguments
        if isinstance(v, (dict, list)):
            try:
                return json.dumps(v, indent=2, ensure_ascii=False)
            except Exception:
                return str(v)
        if isinstance(v, str):
            s = v.strip()
            if s and s[0] in "{[":
                try:
                    return json.dumps(json.loads(s), indent=2, ensure_ascii=False)
                except Exception:
                    return v
            return v
        return str(v)


@dataclass
class UnifiedMessage:
    """A message normalized across all agents.

    Adapter-specific fields go in ``extras`` so they can still be displayed
    without polluting the common schema.
    """

    index: int = 0
    role: str = "?"  # user / assistant / tool / system / ...
    content: str = ""  # flattened printable content
    tool_calls: list[ToolCall] = field(default_factory=list)  # for assistant turns
    tool_name: str | None = None  # for tool-result messages
    tool_call_id: str | None = None  # for tool-result messages (links to assistant call)
    finish_reason: str | None = None
    reasoning: str = ""
    timestamp: float | str | None = None
    token_count: int | None = None
    extras: dict[str, Any] = field(default_factory=dict)


@dataclass
class SessionSummary:
    """One row on the sessions list page.

    Adapter-specific extras (e.g. parent_session_id, project path) live in
    ``extras`` and may be surfaced by the detail page later.
    """

    agent: str = ""  # 'codex' / 'cc' / ...
    session_id: str = ""
    title: str | None = None
    model: str | None = None
    started_at: float | None = None  # unix ts
    ended_at: float | None = None
    message_count: int | None = None
    tool_call_count: int | None = None
    api_call_count: int | None = None
    input_tokens: int | None = None
    output_tokens: int | None = None
    cache_read_tokens: int | None = None
    end_reason: str | None = None
    extras: dict[str, Any] = field(default_factory=dict)

    @property
    def duration_s(self) -> float | None:
        if self.started_at is None or self.ended_at is None:
            return None
        return max(0.0, float(self.ended_at) - float(self.started_at))


@dataclass(frozen=True)
class SessionGroup:
    """One independently paged trace root on the sessions list page."""

    key: str
    source: str
    label: str


@dataclass
class SessionPage:
    """One page of session summaries from a single trace root."""

    sessions: list[SessionSummary]
    next_offset: int | None = None
    session_group_count: int = 0


@dataclass(frozen=True)
class TraceThreadIdentity:
    """Logical session and thread identity for one native trace file."""

    trace_id: str
    session_id: str
    thread_id: str
    parent_thread_id: str | None = None
    kind: str = "main"
    depth: int = 0
    agent_label: str | None = None
    agent_path: str | None = None

    def as_extras(self) -> dict[str, Any]:
        return {
            "group_session_id": self.session_id,
            "thread_id": self.thread_id,
            "parent_thread_id": self.parent_thread_id,
            "thread_kind": self.kind,
            "thread_depth": self.depth,
            "agent_label": self.agent_label,
            "agent_path": self.agent_path,
        }


@dataclass
class SessionDetail:
    """Everything needed to render a single session detail page."""

    summary: SessionSummary
    messages: list[UnifiedMessage] = field(default_factory=list)
    system_prompt: str = ""
    tools_def: list[dict[str, Any]] = field(default_factory=list)
    raw_meta: dict[str, Any] = field(default_factory=dict)  # extra k/v for top metadata grid
    raw_path: str | None = None  # file path of the underlying transcript, if any
    error: str | None = None  # surfaced to UI when partial / failed load


@dataclass
class AdapterStatus:
    """Health / availability info for the list page header."""

    available: bool
    note: str = ""  # short human-readable status
    data_path: str | None = None
