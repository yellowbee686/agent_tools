"""HTTP server: routing, static asset serving, error handling.

Routes (all under unified prefix):

    GET /                                   home (agent picker, or redirect)
    GET /static/<path>                      serve core/static/<path>
    GET /<agent>/?source=all|user|runtime|traces    sessions list
    GET /<agent>/sessions.json?root=<key>&offset=<n>&limit=<n>  paged sessions JSON
    GET /<agent>/session/<session_id>       session detail
    GET /<agent>/session?id=...             redirect to detail
"""

from __future__ import annotations

import dataclasses
import json
import sys
import urllib.parse
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

from . import render
from .adapter import AdapterRegistry
from .models import SessionSummary
from .sources import (
    FILTERABLE_TRACE_SOURCES,
    TRACE_SOURCE_ALL,
    TRACE_SOURCE_ORDER,
)


_STATIC_DIR = Path(__file__).parent / "static"
_STATIC_TYPES = {
    ".css": "text/css; charset=utf-8",
    ".js": "application/javascript; charset=utf-8",
    ".html": "text/html; charset=utf-8",
    ".json": "application/json",
    ".svg": "image/svg+xml",
    ".png": "image/png",
}


def _json_default(obj: Any) -> Any:
    if dataclasses.is_dataclass(obj):
        return dataclasses.asdict(obj)
    if isinstance(obj, Path):
        return str(obj)
    return str(obj)


class _Handler(BaseHTTPRequestHandler):
    server_version = "TraceTracker/0.2"
    registry: AdapterRegistry = None  # type: ignore
    default_agent: str = ""
    default_limit: int = 50

    # ----- low-level helpers ---------------------------------------------------

    def log_message(self, fmt: str, *args: Any) -> None:
        sys.stderr.write(
            f"[{self.log_date_time_string()}] {self.address_string()} {fmt % args}\n"
        )

    def _send(
        self,
        body: str | bytes,
        status: int = 200,
        ctype: str = "text/html; charset=utf-8",
        extra_headers: dict[str, str] | None = None,
    ) -> None:
        if isinstance(body, str):
            body = body.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        for k, v in (extra_headers or {}).items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(body)

    def _redirect(self, location: str) -> None:
        self.send_response(HTTPStatus.FOUND)
        self.send_header("Location", location)
        self.end_headers()

    def _send_error_page(
        self, status: int, message: str, current_agent: str | None = None
    ) -> None:
        page = render.render_error_page(
            registry=self.registry,
            code=status,
            message=message,
            current_agent=current_agent,
        )
        self._send(page, status=status)

    # ----- routing -------------------------------------------------------------

    def do_GET(self) -> None:  # noqa: N802 (BaseHTTPRequestHandler convention)
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path
        qs = urllib.parse.parse_qs(parsed.query)

        try:
            if path == "/":
                self._handle_home()
                return
            if path.startswith("/static/"):
                self._handle_static(path[len("/static/"):])
                return
            # /<agent>/...
            parts = path.strip("/").split("/")
            agent_name = parts[0]
            if agent_name not in self.registry:
                self._send_error_page(
                    HTTPStatus.NOT_FOUND, f"unknown agent: {agent_name!r}"
                )
                return
            adapter = self.registry[agent_name]
            sub = parts[1:]
            if not sub or (len(sub) == 1 and sub[0] == ""):
                self._handle_list(adapter, qs)
                return
            if sub == ["sessions.json"]:
                self._handle_list_json(adapter, qs)
                return
            if sub == ["session"]:
                sid = (qs.get("id") or [""])[0].strip()
                if not sid:
                    self._send_error_page(
                        HTTPStatus.BAD_REQUEST, "missing session id", agent_name
                    )
                    return
                source = self._selected_source(qs)
                suffix = "" if source == "all" else f"?source={urllib.parse.quote(source)}"
                self._redirect(
                    f"/{agent_name}/session/{urllib.parse.quote(sid)}{suffix}"
                )
                return
            if sub[0] == "session" and len(sub) >= 2:
                sid = urllib.parse.unquote("/".join(sub[1:])).strip()
                self._handle_detail(adapter, sid, qs)
                return
            self._send_error_page(HTTPStatus.NOT_FOUND, f"path not found: {path}")
        except Exception as exc:
            tb_msg = f"{type(exc).__name__}: {exc}"
            self.log_message("EXC %s", tb_msg)
            self._send_error_page(HTTPStatus.INTERNAL_SERVER_ERROR, tb_msg)

    # ----- handlers ------------------------------------------------------------

    def _handle_home(self) -> None:
        if self.default_agent and self.default_agent in self.registry:
            self._redirect(f"/{self.default_agent}/")
            return
        page = render.render_home_page(registry=self.registry)
        self._send(page)

    def _handle_static(self, rel: str) -> None:
        if ".." in rel.split("/"):
            self._send_error_page(HTTPStatus.BAD_REQUEST, "invalid static path")
            return
        target = (_STATIC_DIR / rel).resolve()
        try:
            target.relative_to(_STATIC_DIR.resolve())
        except ValueError:
            self._send_error_page(HTTPStatus.BAD_REQUEST, "static path escape")
            return
        if not target.is_file():
            self._send_error_page(HTTPStatus.NOT_FOUND, f"static asset missing: {rel}")
            return
        ctype = _STATIC_TYPES.get(target.suffix, "application/octet-stream")
        self._send(target.read_bytes(), ctype=ctype)

    def _handle_list(self, adapter: Any, qs: dict[str, list[str]]) -> None:
        try:
            limit = max(
                1,
                min(200, int((qs.get("limit") or [str(self.default_limit)])[0])),
            )
        except ValueError:
            limit = self.default_limit
        source = self._selected_source(qs)
        page = render.render_list_page(
            registry=self.registry,
            adapter=adapter,
            sessions=[],
            limit=limit,
            selected_source=source,
            groups=adapter.session_groups(source),
        )
        self._send(page)

    def _handle_list_json(self, adapter: Any, qs: dict[str, list[str]]) -> None:
        try:
            limit = max(
                1,
                min(200, int((qs.get("limit") or [str(self.default_limit)])[0])),
            )
            offset = max(0, int((qs.get("offset") or ["0"])[0]))
        except ValueError:
            limit = self.default_limit
            offset = 0
        source = self._selected_source(qs)
        group_key = (qs.get("root") or [""])[0].strip()
        allowed_groups = {group.key for group in adapter.session_groups(source)}
        if not group_key or group_key not in allowed_groups:
            self._send_error_page(
                HTTPStatus.BAD_REQUEST,
                f"unknown or missing trace root: {group_key!r}",
                adapter.name,
            )
            return
        page = adapter.list_session_page(
            group_key=group_key,
            limit=limit,
            offset=offset,
        )
        show_profile = any(
            (session.extras or {}).get("profile") for session in page.sessions
        )
        payload = {
            "sessions": [dataclasses.asdict(session) for session in page.sessions],
            "rows_html": render.render_session_rows(
                page.sessions, show_profile=show_profile
            ),
            "next_offset": page.next_offset,
            "has_more": page.next_offset is not None,
            "session_group_count": page.session_group_count,
            "thread_count": len(page.sessions),
        }
        body = json.dumps(
            payload,
            indent=2,
            ensure_ascii=False,
            default=_json_default,
        )
        self._send(body, ctype="application/json; charset=utf-8")

    def _handle_detail(
        self, adapter, session_id: str, qs: dict[str, list[str]] | None = None
    ) -> None:
        if not adapter.is_valid_session_id(session_id):
            self._send_error_page(
                HTTPStatus.BAD_REQUEST,
                f"invalid session id for adapter {adapter.name!r}: {session_id!r}",
                adapter.name,
            )
            return
        detail = adapter.load_session_by_source(
            session_id, source=self._selected_source(qs or {})
        )
        if detail is None:
            self._send_error_page(
                HTTPStatus.NOT_FOUND,
                f"session not found in adapter {adapter.name!r}: {session_id}",
                adapter.name,
            )
            return
        page = render.render_detail_page(
            registry=self.registry, adapter=adapter, detail=detail
        )
        self._send(page)

    def _selected_source(self, qs: dict[str, list[str]]) -> str:
        source = (qs.get("source") or [TRACE_SOURCE_ALL])[0].strip().lower()
        if source in FILTERABLE_TRACE_SOURCES:
            return source
        return TRACE_SOURCE_ALL

    def _list_sessions(
        self, adapter: Any, limit: int, source: str
    ) -> list[SessionSummary]:
        if source != TRACE_SOURCE_ALL:
            return adapter.list_sessions_by_source(limit, source)
        sessions = []
        for source_name in TRACE_SOURCE_ORDER:
            sessions.extend(adapter.list_sessions_by_source(limit, source_name))
        sessions.sort(key=lambda summary: -(summary.started_at or 0.0))
        return sessions


def run_server(
    *,
    host: str,
    port: int,
    registry: AdapterRegistry,
    default_agent: str = "",
    default_limit: int = 50,
) -> None:
    if default_agent and default_agent not in registry:
        sys.stderr.write(
            f"[trace-tracker] WARNING: default agent {default_agent!r} not in registry; "
            f"home will show picker.\n"
        )
        default_agent = ""

    _Handler.registry = registry
    _Handler.default_agent = default_agent
    _Handler.default_limit = default_limit

    server = ThreadingHTTPServer((host, port), _Handler)
    sys.stderr.write(
        f"[trace-tracker] serving on http://{host}:{port}/\n"
        f"  agents: {', '.join(registry.names())}\n"
        f"  default agent: {default_agent or '(picker)'}\n"
    )
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        sys.stderr.write("\n[trace-tracker] bye\n")
    finally:
        server.server_close()
