"""HTML rendering.

Rendering is decoupled from data loading: every adapter produces unified
``SessionSummary`` / ``UnifiedMessage`` objects, and these helpers turn them
into HTML strings using the template files in ``core/templates``.
"""

from __future__ import annotations

import datetime as dt
import html
import json
from pathlib import Path
from string import Template
from typing import Any, Iterable

from .adapter import Adapter, AdapterRegistry
from .models import (
    SessionDetail,
    SessionGroup,
    SessionSummary,
    ToolCall,
    UnifiedMessage,
)
from .sources import (
    FILTERABLE_TRACE_SOURCES,
    TRACE_SOURCE_ALL,
    TRACE_SOURCE_CUSTOM,
    TRACE_SOURCE_RUNTIME,
    TRACE_SOURCE_TRACES,
    TRACE_SOURCE_USER,
)


# ---------------------------------------------------------------------------
# Template loading
# ---------------------------------------------------------------------------


_TEMPLATES_DIR = Path(__file__).parent / "templates"


def _read_template(name: str) -> Template:
    return Template((_TEMPLATES_DIR / name).read_text(encoding="utf-8"))


_LAYOUT = _read_template("layout.html")
_LIST = _read_template("list.html")
_DETAIL = _read_template("detail.html")
_ERROR = _read_template("error.html")


# ---------------------------------------------------------------------------
# Formatters
# ---------------------------------------------------------------------------


def fmt_ts(value: Any) -> str:
    if value is None or value == "":
        return ""
    if isinstance(value, (int, float)):
        try:
            return dt.datetime.fromtimestamp(float(value)).strftime("%Y-%m-%d %H:%M:%S")
        except (OverflowError, OSError, ValueError):
            return str(value)
    if isinstance(value, str):
        try:
            return dt.datetime.fromisoformat(value.replace("Z", "+00:00")).strftime(
                "%Y-%m-%d %H:%M:%S"
            )
        except ValueError:
            return value
    return str(value)


def fmt_relative(value: Any) -> str:
    if value is None or value == "":
        return ""
    try:
        secs = float(value)
    except (TypeError, ValueError):
        return ""
    delta = dt.datetime.now().timestamp() - secs
    if delta < 0:
        return "in the future"
    if delta < 60:
        return f"{int(delta)}s ago"
    if delta < 3600:
        return f"{int(delta // 60)}m ago"
    if delta < 86400:
        return f"{int(delta // 3600)}h ago"
    return f"{int(delta // 86400)}d ago"


def fmt_int(value: Any) -> str:
    if value is None or value == "":
        return "—"
    try:
        return f"{int(value):,}"
    except (TypeError, ValueError):
        return str(value)


def fmt_duration(seconds: float | None) -> str:
    if seconds is None:
        return ""
    s = float(seconds)
    if s < 60:
        return f"{s:.1f}s"
    if s < 3600:
        return f"{s / 60:.1f}m"
    if s < 86400:
        return f"{s / 3600:.1f}h"
    return f"{s / 86400:.1f}d"


def _esc(value: Any) -> str:
    return html.escape("" if value is None else str(value))


def _maybe_pretty_json(s: str) -> str:
    s = (s or "").strip()
    if not s:
        return s
    if s[0] in "{[":
        try:
            return json.dumps(json.loads(s), indent=2, ensure_ascii=False)
        except Exception:
            return s
    return s


# ---------------------------------------------------------------------------
# Top-bar / layout helpers
# ---------------------------------------------------------------------------


def _agent_tabs(registry: AdapterRegistry, current: str | None) -> str:
    parts: list[str] = []
    for a in registry:
        cls = ["agent-tabs__link"]
        if current == a.name:
            cls.append("active")
        if not a.status().available:
            cls.append("disabled")
        title = a.description or a.label
        parts.append(
            f'<a class="{" ".join(cls)}" href="/{_esc(a.name)}/" '
            f'title="{_esc(title)}">{_esc(a.label or a.name)}</a>'
        )
    return "".join(parts)


def _search_form(agent: str | None) -> str:
    if not agent:
        return ""
    return (
        f'<form action="/{_esc(agent)}/session" method="get" autocomplete="off">'
        f'<input type="text" name="id" placeholder="session_id" size="42">'
        f'<button type="submit">Open</button>'
        f"</form>"
    )


def _layout(
    *,
    title: str,
    registry: AdapterRegistry,
    current_agent: str | None,
    meta: str,
    content: str,
    scripts: str = "",
) -> str:
    return _LAYOUT.substitute(
        title=_esc(title),
        agent_tabs=_agent_tabs(registry, current_agent),
        meta=meta,  # already-safe string
        search_form=_search_form(current_agent),
        content=content,
        scripts=scripts,
    )


# ---------------------------------------------------------------------------
# List page
# ---------------------------------------------------------------------------


_SOURCE_LABELS = {
    TRACE_SOURCE_ALL: "All roots",
    TRACE_SOURCE_USER: "User directory",
    TRACE_SOURCE_RUNTIME: "runtime traces",
    TRACE_SOURCE_TRACES: "traces directory",
    TRACE_SOURCE_CUSTOM: "Custom root",
}


def _session_source(s: SessionSummary) -> str:
    source = (s.extras or {}).get("trace_source") or TRACE_SOURCE_USER
    return str(source)


def _session_root(s: SessionSummary) -> str:
    root = (s.extras or {}).get("trace_root") or ""
    return str(root)


def _source_picker(agent: str, limit: int, selected_source: str) -> str:
    selected = (
        selected_source
        if selected_source in _SOURCE_LABELS
        else TRACE_SOURCE_ALL
    )
    links: list[str] = []
    for value in (TRACE_SOURCE_ALL, *FILTERABLE_TRACE_SOURCES):
        cls = "source-picker__link"
        if value == selected:
            cls += " active"
        href = f"/{_esc(agent)}/?limit={limit}&source={_esc(value)}"
        links.append(f'<a class="{cls}" href="{href}">{_esc(_SOURCE_LABELS[value])}</a>')
    return '<div class="source-picker">' + "".join(links) + "</div>"


def _short_id(value: str, limit: int = 16) -> str:
    return value if len(value) <= limit else f"{value[:limit]}…"


def _group_session_id(s: SessionSummary) -> str:
    return str((s.extras or {}).get("group_session_id") or s.session_id)


def _thread_kind(s: SessionSummary) -> str:
    return str((s.extras or {}).get("thread_kind") or "main")


def _session_group_key(s: SessionSummary) -> str:
    return f"{_session_source(s)}|{_session_root(s)}|{_group_session_id(s)}"


def _session_row(
    s: SessionSummary,
    *,
    show_profile: bool,
    parent_group_key: str | None = None,
    session_group_key: str | None = None,
    expanded: bool = True,
) -> str:
    profile = (s.extras or {}).get("profile") if show_profile else None
    extras = s.extras or {}
    source = _session_source(s)
    root = _session_root(s)
    group_session_id = _group_session_id(s)
    thread_id = str(extras.get("thread_id") or s.session_id)
    thread_kind = _thread_kind(s)
    thread_depth = int(extras.get("thread_depth") or 0)
    agent_label = str(extras.get("agent_label") or "")
    agent_path = str(extras.get("agent_path") or "")
    search = " ".join(
        _esc(x).lower()
        for x in (
            s.session_id or "",
            group_session_id,
            thread_id,
            thread_kind,
            agent_label,
            agent_path,
            s.title or "",
            s.model or "",
            s.end_reason or "",
            s.agent or "",
            source,
            root,
            str(profile or ""),
        )
    )
    end_reason_html = (
        f'<span class="tag end-reason-{_esc(s.end_reason)}">{_esc(s.end_reason)}</span>'
        if s.end_reason
        else '<span class="muted">—</span>'
    )
    title_html = _esc(s.title) if s.title else '<span class="muted">—</span>'
    started_block = (
        f'<div>{_esc(fmt_ts(s.started_at))}</div>'
        f'<div class="muted" style="font-size:11px">{_esc(fmt_relative(s.started_at))}</div>'
    )
    profile_cell = (
        f'<td><span class="tag profile-tag">{_esc(profile)}</span></td>'
        if show_profile
        else ""
    )
    source_cell = (
        f'<td><span class="tag source-tag source-{_esc(source)}">'
        f"{_esc(source)}</span></td>"
    )
    root_cell = (
        f'<td class="root-cell"><code title="{_esc(root)}">{_esc(root)}</code></td>'
        if root
        else '<td class="root-cell"><span class="muted">—</span></td>'
    )
    source_query = (
        f"?source={_esc(source)}"
        if source in FILTERABLE_TRACE_SOURCES
        else ""
    )
    detail_href = f"/{_esc(s.agent)}/session/{_esc(s.session_id)}{source_query}"
    session_key = f"{source}|{root}|{s.session_id}"
    grouped_thread = parent_group_key is not None
    if grouped_thread:
        role_label = (
            "MAIN" if thread_kind == "main" else f"SUB · d{max(1, thread_depth)}"
        )
        role_class = "main" if thread_kind == "main" else "subagent"
        agent_html = (
            f'<span class="thread-agent-label">{_esc(agent_label)}</span>'
            if agent_label
            else ""
        )
        path_html = (
            f'<span class="thread-agent-path" title="{_esc(agent_path)}">'
            f"{_esc(agent_path)}</span>"
            if agent_path
            else ""
        )
        identity_cell = (
            '<td class="thread-identity-cell">'
            f'<span class="thread-kind thread-kind-{role_class}">{role_label}</span>'
            f"{agent_html}"
            f'<a href="{detail_href}"><code title="{_esc(thread_id)}">'
            f"{_esc(_short_id(thread_id))}</code></a>"
            f"{path_html}</td>"
        )
    else:
        identity_cell = (
            f'<td><a href="{detail_href}"><code>{_esc(s.session_id)}</code></a></td>'
        )
    classes = "thread-row" if grouped_thread else "session-row"
    group_attr = (
        f' data-parent-group-key="{_esc(parent_group_key)}"'
        if parent_group_key
        else f' data-session-group-key="{_esc(session_group_key)}"'
        if session_group_key
        else ""
    )
    hidden_attr = "" if expanded or not grouped_thread else " hidden"
    depth_style = (
        f' style="--thread-depth:{min(8, max(0, thread_depth))}"'
        if grouped_thread
        else ""
    )
    return (
        f'<tr class="{classes}" data-search="{search}" '
        f'data-session-key="{_esc(session_key)}" '
        f'data-thread-key="{_esc(session_key)}"{group_attr}{depth_style}{hidden_attr}>'
        f"{identity_cell}"
        f"{source_cell}"
        f"{root_cell}"
        f"{profile_cell}"
        f"<td>{started_block}</td>"
        f"<td>{_esc(fmt_duration(s.duration_s))}</td>"
        f"<td>{_esc(s.model or '')}</td>"
        f'<td class="num">{fmt_int(s.message_count)}</td>'
        f'<td class="num">{fmt_int(s.tool_call_count)}</td>'
        f'<td class="num">{fmt_int(s.api_call_count)}</td>'
        f'<td class="num">{fmt_int(s.input_tokens)}</td>'
        f'<td class="num">{fmt_int(s.output_tokens)}</td>'
        f'<td class="num">{fmt_int(s.cache_read_tokens)}</td>'
        f"<td>{end_reason_html}</td>"
        f"<td>{title_html}</td>"
        f"</tr>"
    )


def render_session_rows(
    sessions: list[SessionSummary], *, show_profile: bool
) -> str:
    """Render session groups and their server-escaped thread rows."""
    grouped: dict[str, list[SessionSummary]] = {}
    for session in sessions:
        grouped.setdefault(_session_group_key(session), []).append(session)

    col_count = 15 if show_profile else 14
    rows: list[str] = []
    for group_key, threads in grouped.items():
        ordered = sorted(
            threads,
            key=lambda thread: (
                0 if _thread_kind(thread) == "main" else 1,
                int((thread.extras or {}).get("thread_depth") or 0),
                -(thread.started_at or 0.0),
            ),
        )
        has_subagent = any(_thread_kind(thread) == "subagent" for thread in ordered)
        if len(ordered) == 1 and not has_subagent:
            rows.append(
                _session_row(
                    ordered[0],
                    show_profile=show_profile,
                    session_group_key=group_key,
                )
            )
            continue

        main_count = sum(_thread_kind(thread) == "main" for thread in ordered)
        subagent_count = len(ordered) - main_count
        expanded = len(ordered) <= 6
        group_session_id = _group_session_id(ordered[0])
        counts = (
            f"{main_count} main · {subagent_count} "
            f"subagent{'s' if subagent_count != 1 else ''}"
        )
        last_active = max(
            (thread.ended_at or thread.started_at or 0.0 for thread in ordered),
            default=0.0,
        )
        group_search = " ".join(
            _esc(value).lower()
            for value in (
                group_session_id,
                counts,
                *(thread.title or "" for thread in ordered),
                *((thread.extras or {}).get("agent_label") or "" for thread in ordered),
                *((thread.extras or {}).get("agent_path") or "" for thread in ordered),
            )
        )
        disclosure = "▾" if expanded else "▸"
        rows.append(
            f'<tr class="session-group-row" data-search="{group_search}" '
            f'data-session-group-key="{_esc(group_key)}">'
            f'<td colspan="{col_count}"><button type="button" '
            f'class="session-group-toggle" data-group-key="{_esc(group_key)}" '
            f'aria-expanded="{str(expanded).lower()}">'
            f'<span class="session-group-disclosure" aria-hidden="true">'
            f"{disclosure}</span>"
            f'<span class="session-group-id">Session '
            f'<code title="{_esc(group_session_id)}">'
            f"{_esc(_short_id(group_session_id))}</code></span>"
            f'<span class="session-group-counts">{counts}</span>'
            f'<span class="session-group-active">Last active '
            f"{_esc(fmt_ts(last_active))}</span>"
            f"</button></td></tr>"
        )
        rows.extend(
            _session_row(
                thread,
                show_profile=show_profile,
                parent_group_key=group_key,
                expanded=expanded,
            )
            for thread in ordered
        )
    return "\n".join(rows)


def _session_table(
    sessions: list[SessionSummary], *, show_profile: bool, col_count: int
) -> str:
    profile_header = '<th>profile</th>' if show_profile else ""
    if sessions:
        rows_html = render_session_rows(sessions, show_profile=show_profile)
    else:
        rows_html = (
            f'<tr><td colspan="{col_count}" class="empty-state">'
            "No sessions found."
            "</td></tr>"
        )
    return f"""
<table class="sessions">
  <thead>
    <tr>
      <th>session / thread</th>
      <th>source</th>
      <th>root</th>
      {profile_header}
      <th>started</th>
      <th>dur</th>
      <th>model</th>
      <th class="num">msgs</th>
      <th class="num">tools</th>
      <th class="num">api</th>
      <th class="num">in tok</th>
      <th class="num">out tok</th>
      <th class="num">cache in</th>
      <th>end</th>
      <th>title</th>
    </tr>
  </thead>
  <tbody>
{rows_html}
  </tbody>
</table>
"""


def _list_sections(
    sessions: list[SessionSummary], *, selected_source: str, show_profile: bool
) -> str:
    col_count = 15 if show_profile else 14
    if selected_source in FILTERABLE_TRACE_SOURCES:
        title = _SOURCE_LABELS[selected_source]
        return (
            f'<section class="session-section"><h3>{_esc(title)}</h3>'
            + _session_table(sessions, show_profile=show_profile, col_count=col_count)
            + "</section>"
        )

    sections: list[str] = []
    for source in FILTERABLE_TRACE_SOURCES:
        source_sessions = [session for session in sessions if _session_source(session) == source]
        title = f"{_SOURCE_LABELS[source]} ({len(source_sessions)})"
        sections.append(
            f'<section class="session-section"><h3>{_esc(title)}</h3>'
            + _session_table(
                source_sessions, show_profile=show_profile, col_count=col_count
            )
            + "</section>"
        )
    other_sessions = [
        session
        for session in sessions
        if _session_source(session) not in FILTERABLE_TRACE_SOURCES
    ]
    if other_sessions:
        sections.append(
            f'<section class="session-section"><h3>{_esc(_SOURCE_LABELS[TRACE_SOURCE_CUSTOM])} '
            f"({len(other_sessions)})</h3>"
            + _session_table(other_sessions, show_profile=show_profile, col_count=col_count)
            + "</section>"
        )
    return "\n".join(sections)


def _lazy_list_sections(
    groups: list[SessionGroup], *, agent: str, limit: int, show_profile: bool
) -> str:
    col_count = 15 if show_profile else 14
    profile_header = '<th>profile</th>' if show_profile else ""
    sections: list[str] = []
    for group in groups:
        source_label = _SOURCE_LABELS.get(group.source, _SOURCE_LABELS[TRACE_SOURCE_CUSTOM])
        sections.append(
            f"""
<section class="session-section lazy-session-section"
         data-agent="{_esc(agent)}"
         data-root-key="{_esc(group.key)}"
         data-source="{_esc(group.source)}"
         data-page-size="{limit}"
         data-next-offset="0">
  <h3>
    <span>{_esc(source_label)}</span>
    <code title="{_esc(group.label)}">{_esc(group.label)}</code>
    <span class="muted" data-loaded-count>0 loaded</span>
  </h3>
  <table class="sessions">
    <thead>
      <tr>
        <th>session / thread</th><th>source</th><th>root</th>{profile_header}
        <th>started</th><th>dur</th><th>model</th>
        <th class="num">msgs</th><th class="num">tools</th><th class="num">api</th>
        <th class="num">in tok</th><th class="num">out tok</th><th class="num">cache in</th>
        <th>end</th><th>title</th>
      </tr>
    </thead>
    <tbody>
      <tr class="lazy-placeholder">
        <td colspan="{col_count}">
          <span class="skeleton-line"></span><span class="skeleton-line short"></span>
        </td>
      </tr>
    </tbody>
  </table>
  <div class="session-loader" aria-live="polite">Scroll to load sessions</div>
  <button type="button" class="load-more-button">Load {limit} more</button>
  <div class="load-sentinel" aria-hidden="true"></div>
</section>
"""
        )
    if not sections:
        return '<div class="empty-state">No trace roots found for this source.</div>'
    return "\n".join(sections)


def render_list_page(
    *,
    registry: AdapterRegistry,
    adapter: Adapter,
    sessions: list[SessionSummary],
    limit: int,
    selected_source: str = "all",
    groups: list[SessionGroup] | None = None,
) -> str:
    status = adapter.status()
    if status.available:
        status_block = (
            f'<div class="adapter-status">'
            f"adapter <code>{_esc(adapter.name)}</code> · {_esc(status.note or 'ready')}"
            + (f" · <code>{_esc(status.data_path)}</code>" if status.data_path else "")
            + "</div>"
        )
    else:
        status_block = (
            f'<div class="adapter-status unavailable">'
            f"adapter <code>{_esc(adapter.name)}</code> is not yet implemented. "
            f"{_esc(status.note)}"
            + (
                f' Data path: <code>{_esc(status.data_path)}</code>.'
                if status.data_path
                else ""
            )
            + "</div>"
        )

    selected_source = (
        selected_source
        if selected_source in _SOURCE_LABELS
        else TRACE_SOURCE_ALL
    )
    show_profile = any(
        (s.extras or {}).get("profile") for s in sessions
    )
    if groups is None:
        sections_html = _list_sections(
            sessions, selected_source=selected_source, show_profile=show_profile
        )
    else:
        sections_html = _lazy_list_sections(
            groups,
            agent=adapter.name,
            limit=limit,
            show_profile=show_profile,
        )

    body = _LIST.substitute(
        status_block=status_block,
        limit=limit,
        source_picker=_source_picker(adapter.name, limit, selected_source),
        source_label=_SOURCE_LABELS[selected_source],
        source_value=selected_source,
        session_sections=sections_html,
    )
    meta = (
        f'<code>{_esc(adapter.label or adapter.name)}</code> · '
        f'source: <code>{_esc(_SOURCE_LABELS[selected_source])}</code> · '
        f'profile/root: <code>{_esc(status.data_path or "—")}</code>'
    )
    return _layout(
        title=f"{adapter.label or adapter.name} sessions",
        registry=registry,
        current_agent=adapter.name,
        meta=meta,
        content=body,
        scripts='<script src="/static/list.js"></script>',
    )


# ---------------------------------------------------------------------------
# Detail page
# ---------------------------------------------------------------------------


def _render_collapsible_pre(text: str, soft_limit: int = 1500) -> str:
    if not text:
        return '<pre class="content"><span class="muted">(empty)</span></pre>'
    safe = html.escape(text)
    if len(text) <= soft_limit:
        return f'<pre class="content">{safe}</pre>'
    return (
        f'<details class="expandable" open>'
        f"<summary>show full ({len(text):,} chars)</summary>"
        f'<pre class="content">{safe}</pre>'
        f"</details>"
    )


def _render_tool_call(tc: ToolCall) -> str:
    args_pretty = tc.pretty_arguments()
    summary = (
        f"<code>{_esc(tc.name or '(unknown)')}</code>"
        f' <span class="muted">id={_esc(str(tc.id)[:36])}</span>'
    )
    return (
        f'<details class="tool-call" open>'
        f"<summary>{summary}</summary>"
        f"<div>"
        f'<div class="section-label">arguments</div>'
        f'<pre class="content">{html.escape(args_pretty)}</pre>'
        f"</div>"
        f"</details>"
    )


def _render_message(m: UnifiedMessage) -> str:
    head_bits = [
        f'<span class="idx">#{m.index}</span>',
        f'<span class="role-badge role-{_esc(m.role)}">{_esc(m.role)}</span>',
    ]
    if m.tool_name:
        head_bits.append(
            f'<code style="color:var(--tool)">{_esc(m.tool_name)}</code>'
        )
    if m.tool_call_id:
        head_bits.append(f'<span class="muted">→ {_esc(str(m.tool_call_id)[:36])}</span>')
    if m.finish_reason:
        head_bits.append(f'<span class="tag">finish: {_esc(m.finish_reason)}</span>')
    if m.timestamp:
        head_bits.append(f'<span class="muted">{_esc(fmt_ts(m.timestamp))}</span>')
    if m.token_count:
        head_bits.append(f'<span class="tag">tok: {fmt_int(m.token_count)}</span>')

    body_parts: list[str] = []

    if m.reasoning:
        body_parts.append('<div class="section-label">reasoning</div>')
        body_parts.append(_render_collapsible_pre(m.reasoning, soft_limit=1200))

    if m.role == "tool" and m.content:
        body_parts.append('<div class="section-label">result</div>')
        body_parts.append(_render_collapsible_pre(_maybe_pretty_json(m.content), soft_limit=1200))
    elif m.content or (not m.tool_calls and m.role != "tool"):
        body_parts.append('<div class="section-label">content</div>')
        body_parts.append(_render_collapsible_pre(m.content, soft_limit=2000))

    if m.tool_calls:
        body_parts.append(
            f'<div class="section-label">tool_calls ({len(m.tool_calls)})</div>'
        )
        for tc in m.tool_calls:
            body_parts.append(_render_tool_call(tc))

    search_text = " ".join(
        x.lower()
        for x in (
            m.content or "",
            m.reasoning or "",
            m.tool_name or "",
            str(m.tool_call_id or ""),
            m.finish_reason or "",
            json.dumps(
                [{"name": tc.name, "args": tc.pretty_arguments()} for tc in m.tool_calls],
                ensure_ascii=False,
            )
            if m.tool_calls
            else "",
        )
    )

    anchor = ""
    if m.tool_call_id:
        anchor = f' id="call-{_esc(m.tool_call_id)}"'

    return (
        f'<div class="message" data-role="{_esc(m.role)}" '
        f'data-search="{html.escape(search_text)}"{anchor}>'
        f'<div class="head">{"".join(head_bits)}</div>'
        f'<div class="body">{"".join(body_parts) or chr(0)}</div>'
        f"</div>"
    ).replace(chr(0), '<span class="muted">(no body)</span>')


def render_detail_page(
    *,
    registry: AdapterRegistry,
    adapter: Adapter,
    detail: SessionDetail,
) -> str:
    s = detail.summary

    # ----- metadata grid -----
    base_kv: list[tuple[str, Any]] = [
        ("session_id", s.session_id),
        ("model", s.model or "—"),
        ("started", fmt_ts(s.started_at)),
        ("ended", fmt_ts(s.ended_at)),
        ("duration", fmt_duration(s.duration_s)),
        ("messages", fmt_int(s.message_count or len(detail.messages))),
        ("tool calls", fmt_int(s.tool_call_count)),
        ("api calls", fmt_int(s.api_call_count)),
        ("input tokens", fmt_int(s.input_tokens)),
        ("output tokens", fmt_int(s.output_tokens)),
        ("cache read", fmt_int(s.cache_read_tokens)),
        ("end_reason", s.end_reason or "—"),
    ]
    for k, v in detail.raw_meta.items():
        base_kv.append((k, v))

    metadata_rows = "\n".join(
        f'<div class="kv-row"><span class="k">{_esc(k)}:</span>'
        f'<span class="v">{_esc(v)}</span></div>'
        for k, v in base_kv
    )

    # ----- role mix -----
    role_counts: dict[str, int] = {}
    for m in detail.messages:
        role_counts[m.role] = role_counts.get(m.role, 0) + 1
    role_chips = " ".join(
        f'<span class="tag">{_esc(r)}: {fmt_int(n)}</span>'
        for r, n in sorted(role_counts.items(), key=lambda x: -x[1])
    )

    # ----- actions -----
    actions: list[str] = []
    if detail.raw_path:
        actions.append(
            f'<a href="file://{_esc(detail.raw_path)}">open raw transcript</a>'
        )

    # ----- system prompt -----
    if detail.system_prompt:
        sp = detail.system_prompt
        sysprompt_block = (
            f'<details class="message expandable">'
            f'<summary class="head" style="background:var(--bg-3);padding:8px 14px;cursor:pointer">'
            f'<span class="role-badge role-system">system</span>'
            f'<span class="muted">system prompt ({len(sp):,} chars)</span>'
            f"</summary>"
            f'<div class="body">{_render_collapsible_pre(sp, soft_limit=2000)}</div>'
            f"</details>"
        )
    else:
        sysprompt_block = ""

    # ----- tools registered -----
    if detail.tools_def:
        tool_names = []
        for t in detail.tools_def:
            try:
                tool_names.append(
                    (t.get("function") or {}).get("name") or t.get("name") or "?"
                )
            except Exception:
                tool_names.append("?")
        sample = ", ".join(_esc(n) for n in tool_names[:30])
        more = "…" if len(tool_names) > 30 else ""
        tools_block = (
            f'<details class="expandable">'
            f"<summary>tools registered: {len(detail.tools_def)} ({sample}{more})</summary>"
            f'<pre class="content">{_esc(json.dumps(detail.tools_def, indent=2, ensure_ascii=False))}</pre>'
            f"</details>"
        )
    else:
        tools_block = ""

    # ----- error -----
    error_block = (
        f'<div class="error-box">{_esc(detail.error)}</div>' if detail.error else ""
    )

    # ----- role filter options -----
    seen_roles = list(role_counts.keys())
    standard = ["user", "assistant", "tool", "system"]
    options = [r for r in standard if r in seen_roles] + [
        r for r in seen_roles if r not in standard
    ]
    role_options = "\n".join(
        f'<option value="{_esc(r)}">{_esc(r)} ({role_counts.get(r, 0)})</option>'
        for r in options
    )

    # ----- messages -----
    messages_html = "\n".join(_render_message(m) for m in detail.messages) or (
        '<div class="empty-state">No messages.</div>'
    )

    body = _DETAIL.substitute(
        error_block=error_block,
        title=_esc(s.title or f"Session {s.session_id}"),
        metadata_rows=metadata_rows,
        actions="\n".join(actions),
        role_chips=role_chips,
        system_prompt_block=sysprompt_block,
        tools_block=tools_block,
        role_options=role_options,
        message_count=len(detail.messages),
        messages=messages_html,
    )

    meta = f'<code>{_esc(adapter.label or adapter.name)}</code> · session <code>{_esc(s.session_id)}</code>'
    return _layout(
        title=f"{s.title or s.session_id} — {adapter.label or adapter.name}",
        registry=registry,
        current_agent=adapter.name,
        meta=meta,
        content=body,
        scripts='<script src="/static/detail.js"></script>',
    )


# ---------------------------------------------------------------------------
# Error page
# ---------------------------------------------------------------------------


def render_error_page(
    *,
    registry: AdapterRegistry,
    code: int,
    message: str,
    current_agent: str | None = None,
) -> str:
    body = _ERROR.substitute(code=code, message=_esc(message))
    return _layout(
        title=f"Error {code}",
        registry=registry,
        current_agent=current_agent,
        meta="",
        content=body,
    )


# ---------------------------------------------------------------------------
# Home page (no agent selected)
# ---------------------------------------------------------------------------


def render_home_page(*, registry: AdapterRegistry) -> str:
    cards: list[str] = []
    for a in registry:
        st = a.status()
        badge = (
            '<span class="status-pill ready">ready</span>'
            if st.available
            else '<span class="status-pill unavailable">unavailable</span>'
        )
        note_html = (
            f'<div class="agent-card__note">{_esc(st.note)}</div>' if st.note else ""
        )
        path_html = (
            f'<div class="agent-card__path"><code>{_esc(st.data_path)}</code></div>'
            if st.data_path
            else ""
        )
        cards.append(
            f'<a href="/{_esc(a.name)}/" class="agent-card">'
            f'<div class="agent-card__title">'
            f'<span class="agent-card__name">{_esc(a.label or a.name)}</span>'
            f"{badge}"
            f"</div>"
            f'<div class="agent-card__desc">{_esc(a.description or "")}</div>'
            f"{note_html}"
            f"{path_html}"
            f'<div class="agent-card__cta">browse sessions →</div>'
            f"</a>"
        )
    body = (
        '<section class="landing">'
        '<h2 class="landing__h">Pick an agent to inspect</h2>'
        '<p class="landing__sub">'
        "Each card is one of the supported agents. Click to open its session list, "
        "or use the tabs in the top bar at any time."
        "</p>"
        f'<div class="landing__cards">{"".join(cards)}</div>'
        "</section>"
    )
    return _layout(
        title="Trace Tracker",
        registry=registry,
        current_agent=None,
        meta="",
        content=body,
    )
