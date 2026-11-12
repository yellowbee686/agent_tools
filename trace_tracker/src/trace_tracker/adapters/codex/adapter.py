"""Codex CLI adapter — list + detail backed by the JSONL parser."""

from __future__ import annotations

import re
from pathlib import Path

from ...core.adapter import Adapter
from ...core.cache import MtimeCache
from ...core.models import (
    AdapterStatus,
    SessionDetail,
    SessionPage,
    SessionSummary,
    TraceThreadIdentity,
)
from . import parser


_CODEX_SESSION_ID_RE = re.compile(r"^rollout-[A-Za-z0-9._\-]{1,200}$")


class CodexAdapter(Adapter):
    name = "codex"
    label = "Codex"
    description = (
        "OpenAI Codex CLI / Desktop — reads ~/.codex/sessions/<YYYY>/<MM>/<DD>/."
    )

    def __init__(self, *, root: Path) -> None:
        self.root = Path(root).expanduser()
        self._cache: MtimeCache[SessionDetail] = MtimeCache(parser.parse)
        self._identity_cache: MtimeCache[TraceThreadIdentity] = MtimeCache(
            parser.parse_thread_identity
        )
        self._path_index: dict[str, Path] = {}  # session_id -> path

    @property
    def sessions_dir(self) -> Path:
        return self.root / "sessions"

    # ----- adapter API ---------------------------------------------------------

    def status(self) -> AdapterStatus:
        if not self.sessions_dir.exists():
            return AdapterStatus(
                available=False,
                note="~/.codex/sessions not found",
                data_path=str(self.sessions_dir),
            )
        return AdapterStatus(
            available=True,
            note=str(self.sessions_dir),
            data_path=str(self.sessions_dir),
        )

    def is_valid_session_id(self, session_id: str) -> bool:
        return bool(_CODEX_SESSION_ID_RE.match(session_id or ""))

    def list_sessions(self, limit: int) -> list[SessionSummary]:
        files = self._collect_files(limit)
        out: list[SessionSummary] = []
        for f in files:
            self._path_index[f.stem] = f
            detail = self._cache.get(f)
            if detail is None:
                continue
            out.append(detail.summary)
        return out

    def load_session(self, session_id: str) -> SessionDetail | None:
        path = self._path_index.get(session_id) or self._find_path(session_id)
        if path is None:
            return None
        return self._cache.get(path)

    def list_session_page(
        self, *, group_key: str, limit: int, offset: int
    ) -> SessionPage:
        if group_key != "0":
            raise KeyError(f"unknown session group: {group_key}")
        groups = self._collect_session_groups()
        selected = groups[offset : offset + limit + 1]
        page_groups = selected[:limit]
        sessions: list[SessionSummary] = []
        for paths in page_groups:
            for path in sorted(paths, key=self._thread_sort_key):
                self._path_index[path.stem] = path
                detail = self._cache.get(path)
                if detail is not None:
                    sessions.append(detail.summary)
        next_offset = offset + len(page_groups) if len(selected) > limit else None
        return SessionPage(
            sessions=sessions,
            next_offset=next_offset,
            session_group_count=len(page_groups),
        )

    # ----- internals -----------------------------------------------------------

    def _collect_files(self, limit: int | None) -> list[Path]:
        if not self.sessions_dir.exists():
            return []
        files: list[Path] = []
        for f in self.sessions_dir.rglob("*.jsonl"):
            if f.is_file():
                files.append(f)
        files.sort(
            key=lambda p: (p.stat().st_mtime if p.exists() else 0), reverse=True
        )
        return files if limit is None else files[:limit]

    def _collect_session_groups(self) -> list[list[Path]]:
        groups: dict[str, list[Path]] = {}
        for path in self._collect_files(None):
            identity = self._identity_cache.get(path)
            group_id = identity.session_id if identity is not None else path.stem
            groups.setdefault(group_id, []).append(path)
        return list(groups.values())

    def _thread_sort_key(self, path: Path) -> tuple[int, int, float]:
        identity = self._identity_cache.get(path)
        if identity is None:
            return (0, 0, -(path.stat().st_mtime if path.exists() else 0.0))
        return (
            0 if identity.kind == "main" else 1,
            identity.depth,
            -(path.stat().st_mtime if path.exists() else 0.0),
        )

    def _find_path(self, session_id: str) -> Path | None:
        if not self.sessions_dir.exists():
            return None
        # Most files are under rollout-<id>.jsonl, so do a targeted glob first.
        candidates = list(self.sessions_dir.rglob(f"{session_id}.jsonl"))
        if candidates:
            return candidates[0]
        for f in self.sessions_dir.rglob("*.jsonl"):
            if f.stem == session_id:
                return f
        return None
