"""Adapter base class and registry helpers.

Each agent (codex / cc / ...) implements one ``Adapter`` subclass.
The server consumes adapters through this uniform interface only.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import re
from typing import Iterable

from .models import (
    AdapterStatus,
    SessionDetail,
    SessionGroup,
    SessionPage,
    SessionSummary,
)
from .sources import TRACE_SOURCE_ALL, TRACE_SOURCE_USER


# Default permissive id pattern — adapters can override for stricter validation.
_DEFAULT_SESSION_ID_RE = re.compile(r"^[A-Za-z0-9._\-]{1,128}$")


@dataclass(frozen=True)
class AdapterRoot:
    """One native agent root plus its UI source classification."""

    path: Path
    source: str = TRACE_SOURCE_USER
    label: str | None = None


class Adapter:
    """Base class. Subclasses must override the marked methods."""

    name: str = ""  # url-safe slug, e.g. 'codex'
    label: str = ""  # display label, e.g. 'Codex'
    description: str = ""  # one-line description
    root_source: str = TRACE_SOURCE_USER
    root_label: str | None = None

    # ----- discovery / health --------------------------------------------------

    def status(self) -> AdapterStatus:
        """Return availability info shown above the sessions list."""
        return AdapterStatus(available=False, note="not implemented yet")

    # ----- listing -------------------------------------------------------------

    def list_sessions(self, limit: int) -> list[SessionSummary]:
        """Return the most recent ``limit`` session summaries (newest first)."""
        return []

    def list_sessions_by_source(
        self, limit: int, source: str | None
    ) -> list[SessionSummary]:
        """Return sessions for one root source, or all sessions if unset."""
        if source in (None, "", TRACE_SOURCE_ALL):
            return self.list_sessions(limit)
        return [
            session
            for session in self.list_sessions(limit)
            if (session.extras or {}).get("trace_source", self.root_source) == source
        ]

    def session_groups(self, source: str | None = None) -> list[SessionGroup]:
        """Return independently paged roots available for the selected source."""
        if source not in (None, "", TRACE_SOURCE_ALL, self.root_source):
            return []
        status = self.status()
        label = self.root_label or status.data_path or status.note or self.label
        return [SessionGroup(key="0", source=self.root_source, label=label)]

    def list_session_page(
        self, *, group_key: str, limit: int, offset: int
    ) -> SessionPage:
        """Return one root page without exposing an adapter-specific cursor."""
        if group_key != "0":
            raise KeyError(f"unknown session group: {group_key}")
        candidates = self.list_sessions(offset + limit + 1)
        sessions = candidates[offset : offset + limit]
        next_offset = (
            offset + len(sessions) if len(candidates) > offset + limit else None
        )
        return SessionPage(
            sessions=sessions,
            next_offset=next_offset,
            session_group_count=len(sessions),
        )

    # ----- detail --------------------------------------------------------------

    def load_session(self, session_id: str) -> SessionDetail | None:
        """Return full session detail, or ``None`` if not found."""
        return None

    def load_session_by_source(
        self, session_id: str, source: str | None = None
    ) -> SessionDetail | None:
        """Return session detail, optionally scoped to one root source."""
        return self.load_session(session_id)

    # ----- validation ----------------------------------------------------------

    def is_valid_session_id(self, session_id: str) -> bool:
        """Reject obviously unsafe ids (path traversal etc.)."""
        if not session_id or ".." in session_id or "/" in session_id:
            return False
        return bool(_DEFAULT_SESSION_ID_RE.match(session_id))


class AdapterRegistry:
    """Ordered map of name -> Adapter, preserving insertion order."""

    def __init__(self, adapters: Iterable[Adapter]):
        self._adapters: dict[str, Adapter] = {a.name: a for a in adapters}

    def __iter__(self):
        return iter(self._adapters.values())

    def __contains__(self, name: str) -> bool:
        return name in self._adapters

    def __getitem__(self, name: str) -> Adapter:
        return self._adapters[name]

    def get(self, name: str) -> Adapter | None:
        return self._adapters.get(name)

    def names(self) -> list[str]:
        return list(self._adapters.keys())

    def items(self) -> list[tuple[str, Adapter]]:
        return list(self._adapters.items())


class MultiRootAdapter(Adapter):
    """Expose several same-agent roots as one adapter."""

    def __init__(self, adapters: Iterable[Adapter]) -> None:
        self.adapters = list(adapters)
        if not self.adapters:
            raise ValueError("MultiRootAdapter requires at least one adapter")
        first = self.adapters[0]
        self.name = first.name
        self.label = first.label
        self.description = first.description
        if len(self.adapters) > 1:
            self.description = f"{first.description} Multiple roots are aggregated."

    def _root_label(self, adapter: Adapter) -> str:
        if adapter.root_label:
            return adapter.root_label
        return adapter.status().data_path or adapter.status().note or ""

    def _annotate_summary(self, adapter: Adapter, summary: SessionSummary) -> None:
        root_label = self._root_label(adapter)
        if root_label:
            summary.extras.setdefault("trace_root", root_label)
        summary.extras.setdefault("trace_source", adapter.root_source)

    def _annotate_detail(self, adapter: Adapter, detail: SessionDetail) -> None:
        root_label = self._root_label(adapter)
        if root_label:
            detail.summary.extras.setdefault("trace_root", root_label)
            detail.raw_meta.setdefault("trace_root", root_label)
        detail.summary.extras.setdefault("trace_source", adapter.root_source)
        detail.raw_meta.setdefault("trace_source", adapter.root_source)

    def status(self) -> AdapterStatus:
        if len(self.adapters) == 1:
            return self.adapters[0].status()
        statuses = [adapter.status() for adapter in self.adapters]
        available = [status for status in statuses if status.available]
        # Multi-root setups discover many roots; only report the count instead
        # of rendering every path on the home card and list-page header.
        return AdapterStatus(
            available=bool(available),
            note=f"{len(available)}/{len(statuses)} root(s) available",
        )

    def list_sessions(self, limit: int) -> list[SessionSummary]:
        merged: list[SessionSummary] = []
        for adapter in self.adapters:
            for summary in adapter.list_sessions(limit):
                self._annotate_summary(adapter, summary)
                merged.append(summary)
        merged.sort(key=lambda summary: -(summary.started_at or 0.0))
        return merged[:limit]

    def list_sessions_by_source(
        self, limit: int, source: str | None
    ) -> list[SessionSummary]:
        if source in (None, "", TRACE_SOURCE_ALL):
            return self.list_sessions(limit)

        merged: list[SessionSummary] = []
        for adapter in self.adapters:
            if adapter.root_source != source:
                continue
            for summary in adapter.list_sessions(limit):
                self._annotate_summary(adapter, summary)
                merged.append(summary)
        merged.sort(key=lambda summary: -(summary.started_at or 0.0))
        return merged[:limit]

    def session_groups(self, source: str | None = None) -> list[SessionGroup]:
        groups: list[SessionGroup] = []
        for index, adapter in enumerate(self.adapters):
            if source not in (None, "", TRACE_SOURCE_ALL, adapter.root_source):
                continue
            groups.append(
                SessionGroup(
                    key=str(index),
                    source=adapter.root_source,
                    label=self._root_label(adapter),
                )
            )
        return groups

    def list_session_page(
        self, *, group_key: str, limit: int, offset: int
    ) -> SessionPage:
        try:
            index = int(group_key)
            if index < 0:
                raise IndexError(index)
            adapter = self.adapters[index]
        except (ValueError, IndexError) as exc:
            raise KeyError(f"unknown session group: {group_key}") from exc
        page = adapter.list_session_page(group_key="0", limit=limit, offset=offset)
        for summary in page.sessions:
            self._annotate_summary(adapter, summary)
        return page

    def load_session(self, session_id: str) -> SessionDetail | None:
        for adapter in self.adapters:
            detail = adapter.load_session(session_id)
            if detail is not None:
                self._annotate_detail(adapter, detail)
                return detail
        return None

    def load_session_by_source(
        self, session_id: str, source: str | None = None
    ) -> SessionDetail | None:
        adapters = self.adapters
        if source not in (None, "", "all"):
            adapters = [adapter for adapter in self.adapters if adapter.root_source == source]
        for adapter in adapters:
            detail = adapter.load_session(session_id)
            if detail is not None:
                self._annotate_detail(adapter, detail)
                return detail
        return None

    def is_valid_session_id(self, session_id: str) -> bool:
        return any(adapter.is_valid_session_id(session_id) for adapter in self.adapters)
