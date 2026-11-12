"""Claude Code (cc) JSONL transcript parser.

Schema (observed under ``~/.claude/projects/<encoded-cwd>/<session_id>.jsonl``):

  Top-level record types:
    - ``user``        : message envelope. ``message.content`` is either a str
                        or a list of content blocks (type ∈ tool_result, text,
                        image). Records with ``isMeta: true`` (e.g.
                        ``<local-command-caveat>...``) are skipped.
    - ``assistant``   : message envelope. ``message.content`` is a list of
                        ``thinking`` / ``redacted_thinking`` / ``tool_use`` /
                        ``text`` blocks. Carries ``message.model`` and
                        ``message.usage`` (input_tokens, output_tokens,
                        cache_read_input_tokens, cache_creation_input_tokens).
    - ``system``      : sub-events like ``turn_duration`` — skipped (could be
                        surfaced later as inline annotations).
    - ``attachment``  : hook stdout / additional_context. Often huge and noisy
                        — skipped from the message stream.
    - ``queue-operation``: text typed by the user while a turn was running.
                           Surfaced as a user message tagged ``queued``.
    - ``last-prompt`` / ``permission-mode`` / ``file-history-snapshot``:
                        bookkeeping; not rendered.
"""

from __future__ import annotations

import datetime as dt
import json
import logging
import re
from pathlib import Path
from typing import Any

from ...core.models import (
    SessionDetail,
    SessionSummary,
    ToolCall,
    TraceThreadIdentity,
    UnifiedMessage,
)


LOGGER = logging.getLogger(__name__)
_SESSION_ID_RE = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$"
)


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------


def parse_thread_identity(path: Path) -> TraceThreadIdentity | None:
    """Read only enough metadata to identify a Claude Code agent thread."""
    if not path.is_file():
        return None
    try:
        fh = path.open("r", encoding="utf-8", errors="replace")
    except OSError:
        return None
    with fh:
        for line in fh:
            try:
                record = json.loads(line)
            except Exception:
                continue
            session_id = record.get("sessionId")
            if not session_id:
                continue
            is_subagent = bool(record.get("isSidechain")) or path.stem.startswith(
                "agent-"
            )
            agent_id = record.get("agentId")
            thread_id = path.stem if is_subagent else str(session_id)
            agent_label = record.get("slug") or agent_id
            return TraceThreadIdentity(
                trace_id=path.stem,
                session_id=str(session_id),
                thread_id=thread_id,
                parent_thread_id=str(session_id) if is_subagent else None,
                kind="subagent" if is_subagent else "main",
                depth=1 if is_subagent else 0,
                agent_label=str(agent_label) if agent_label else None,
                agent_path=str(record.get("cwd")) if record.get("cwd") else None,
            )

    inferred_session_id = _session_id_from_path(path)
    if inferred_session_id is None:
        return TraceThreadIdentity(
            trace_id=path.stem,
            session_id=path.stem,
            thread_id=path.stem,
        )
    is_subagent = path.stem.startswith("agent-")
    return TraceThreadIdentity(
        trace_id=path.stem,
        session_id=inferred_session_id,
        thread_id=path.stem if is_subagent else inferred_session_id,
        parent_thread_id=inferred_session_id if is_subagent else None,
        kind="subagent" if is_subagent else "main",
        depth=1 if is_subagent else 0,
    )


def parse(
    path: Path,
    *,
    context_path: Path | None = None,
) -> SessionDetail | None:
    """Parse a single Claude Code JSONL into a SessionDetail."""
    if not path.is_file():
        return None

    session_id = path.stem
    identity = parse_thread_identity(path)
    if identity is None:
        return None
    messages: list[UnifiedMessage] = []

    first_ts: str | None = None
    last_ts: str | None = None
    cwd: str | None = None
    git_branch: str | None = None
    version: str | None = None
    last_model: str | None = None
    first_user_text: str | None = None

    api_call_count = 0
    tool_call_count = 0
    input_tokens_total = 0
    output_tokens_total = 0
    cache_read_total = 0
    cache_creation_total = 0

    try:
        fh = path.open("r", encoding="utf-8", errors="replace")
    except OSError:
        return None

    with fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except Exception:
                continue

            t = rec.get("type")
            ts = rec.get("timestamp")
            if ts:
                if first_ts is None:
                    first_ts = ts
                last_ts = ts

            if not cwd and rec.get("cwd"):
                cwd = rec["cwd"]
            if not git_branch and rec.get("gitBranch"):
                git_branch = rec["gitBranch"]
            if not version and rec.get("version"):
                version = rec["version"]

            if t == "user":
                if rec.get("isMeta"):
                    continue
                for m in _convert_user_record(rec, len(messages)):
                    messages.append(m)
                    if (
                        first_user_text is None
                        and m.role == "user"
                        and m.content
                        and not _looks_like_meta(m.content)
                    ):
                        first_user_text = m.content
            elif t == "assistant":
                m = _convert_assistant_record(rec, len(messages))
                if m is None:
                    continue
                api_call_count += 1
                tool_call_count += len(m.tool_calls)
                msg = rec.get("message") or {}
                usage = msg.get("usage") or {}
                input_tokens_total += int(usage.get("input_tokens") or 0)
                output_tokens_total += int(usage.get("output_tokens") or 0)
                cache_read_total += int(usage.get("cache_read_input_tokens") or 0)
                cache_creation_total += int(
                    usage.get("cache_creation_input_tokens") or 0
                )
                if msg.get("model"):
                    last_model = msg["model"]
                messages.append(m)
            elif t == "queue-operation":
                op = rec.get("operation")
                content = rec.get("content") or ""
                if op == "enqueue" and content:
                    messages.append(
                        UnifiedMessage(
                            index=len(messages),
                            role="user",
                            content=content,
                            timestamp=ts,
                            extras={"queue_operation": op},
                        )
                    )
                    if first_user_text is None:
                        first_user_text = content
            # system / attachment / last-prompt / permission-mode / file-history-snapshot
            # are intentionally skipped.

    title = _first_line(first_user_text, limit=120) if first_user_text else None
    system_prompt, tools_def = _load_request_context(
        context_path or path.with_suffix(".context.json")
    )

    raw_meta: dict[str, Any] = {}
    raw_meta.update(identity.as_extras())
    if cwd:
        raw_meta["cwd"] = cwd
    if git_branch:
        raw_meta["gitBranch"] = git_branch
    if version:
        raw_meta["cli_version"] = version
    if cache_creation_total:
        raw_meta["cache_creation_tokens"] = f"{cache_creation_total:,}"

    summary = SessionSummary(
        agent="cc",
        session_id=session_id,
        title=title,
        model=last_model,
        started_at=_iso_to_unix(first_ts),
        ended_at=_iso_to_unix(last_ts),
        message_count=len(messages),
        tool_call_count=tool_call_count,
        api_call_count=api_call_count,
        input_tokens=input_tokens_total or None,
        output_tokens=output_tokens_total or None,
        cache_read_tokens=cache_read_total or None,
        extras={
            **identity.as_extras(),
            "project": path.parent.name,
            "cache_creation_tokens": cache_creation_total,
            "cwd": cwd,
            "gitBranch": git_branch,
        },
    )

    return SessionDetail(
        summary=summary,
        messages=messages,
        system_prompt=system_prompt,
        tools_def=tools_def,
        raw_meta=raw_meta,
        raw_path=str(path),
    )


def _session_id_from_path(path: Path) -> str | None:
    if _SESSION_ID_RE.match(path.stem):
        return path.stem
    for parent in path.parents:
        if _SESSION_ID_RE.match(parent.name):
            return parent.name
    return None


# ---------------------------------------------------------------------------
# Internals
# ---------------------------------------------------------------------------


def _load_request_context(
    context_path: Path,
) -> tuple[str, list[dict[str, Any]]]:
    if not context_path.is_file():
        return "", []
    try:
        value = json.loads(context_path.read_text(encoding="utf-8"))
    except OSError as error:
        LOGGER.error("Unable to read Claude context %s: %s", context_path, error)
        return "", []
    except json.JSONDecodeError as error:
        LOGGER.error("Unable to parse Claude context %s: %s", context_path, error)
        return "", []
    if not isinstance(value, dict):
        return "", []
    system_prompt = value.get("system_prompt")
    raw_tools = value.get("tools")
    tools = (
        [
            _normalize_claude_tool(tool)
            for tool in raw_tools
            if isinstance(tool, dict)
        ]
        if isinstance(raw_tools, list)
        else []
    )
    return (
        system_prompt if isinstance(system_prompt, str) else "",
        tools,
    )


def _normalize_claude_tool(tool: dict[str, Any]) -> dict[str, Any]:
    return {
        "type": "function",
        "function": {
            "name": tool.get("name") or "?",
            "description": tool.get("description") or "",
            "parameters": tool.get("input_schema") or {},
        },
    }


def _convert_user_record(rec: dict[str, Any], base_idx: int) -> list[UnifiedMessage]:
    msg = rec.get("message") or {}
    content = msg.get("content")
    ts = rec.get("timestamp")

    if isinstance(content, str):
        return [
            UnifiedMessage(
                index=base_idx,
                role="user",
                content=content,
                timestamp=ts,
            )
        ]

    if not isinstance(content, list):
        return []

    out: list[UnifiedMessage] = []
    text_parts: list[str] = []
    for it in content:
        if not isinstance(it, dict):
            text_parts.append(str(it))
            continue
        it_type = it.get("type")
        if it_type == "tool_result":
            tool_content = _flatten_tool_result(it.get("content"))
            out.append(
                UnifiedMessage(
                    index=base_idx + len(out),
                    role="tool",
                    content=tool_content,
                    tool_call_id=it.get("tool_use_id"),
                    timestamp=ts,
                    extras={
                        "is_error": bool(it.get("is_error")),
                    },
                )
            )
        elif it_type == "text":
            text_parts.append(it.get("text") or "")
        elif it_type == "image":
            text_parts.append("[image]")
        else:
            text_parts.append(json.dumps(it, ensure_ascii=False, indent=2))

    if text_parts:
        text = "\n".join(p for p in text_parts if p)
        if text:
            out.append(
                UnifiedMessage(
                    index=base_idx + len(out),
                    role="user",
                    content=text,
                    timestamp=ts,
                )
            )
    return out


def _convert_assistant_record(
    rec: dict[str, Any], idx: int
) -> UnifiedMessage | None:
    msg = rec.get("message") or {}
    content = msg.get("content") or []
    ts = rec.get("timestamp")

    text_parts: list[str] = []
    reasoning_parts: list[str] = []
    tool_calls: list[ToolCall] = []

    if not isinstance(content, list):
        return None

    for it in content:
        if not isinstance(it, dict):
            text_parts.append(str(it))
            continue
        t = it.get("type")
        if t == "text":
            text_parts.append(it.get("text") or "")
        elif t == "thinking":
            reasoning_parts.append(it.get("thinking") or "")
        elif t == "redacted_thinking":
            reasoning_parts.append("(redacted thinking)")
        elif t == "tool_use":
            tool_calls.append(
                ToolCall(
                    id=str(it.get("id") or ""),
                    name=str(it.get("name") or ""),
                    arguments=it.get("input") if it.get("input") is not None else {},
                )
            )
        else:
            text_parts.append(json.dumps(it, ensure_ascii=False, indent=2))

    usage = msg.get("usage") or {}
    return UnifiedMessage(
        index=idx,
        role="assistant",
        content="\n".join(p for p in text_parts if p),
        tool_calls=tool_calls,
        reasoning="\n\n".join(p for p in reasoning_parts if p),
        finish_reason=msg.get("stop_reason"),
        token_count=usage.get("output_tokens"),
        timestamp=ts,
        extras={
            "model": msg.get("model"),
            "input_tokens": usage.get("input_tokens"),
            "cache_read_input_tokens": usage.get("cache_read_input_tokens"),
            "cache_creation_input_tokens": usage.get("cache_creation_input_tokens"),
        },
    )


def _flatten_tool_result(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    if isinstance(value, list):
        out: list[str] = []
        for it in value:
            if isinstance(it, dict):
                if it.get("type") == "text":
                    out.append(it.get("text") or "")
                elif it.get("type") == "image":
                    out.append("[image]")
                else:
                    out.append(json.dumps(it, ensure_ascii=False, indent=2))
            else:
                out.append(str(it))
        return "\n".join(out)
    return json.dumps(value, ensure_ascii=False, indent=2)


def _looks_like_meta(text: str) -> bool:
    """Skip caveats and command stubs when picking the title."""
    head = (text or "").lstrip()[:80].lower()
    if head.startswith("<command-name>"):
        return True
    if head.startswith("<local-command"):
        return True
    if head.startswith("caveat:"):
        return True
    return False


def _first_line(text: str | None, limit: int = 120) -> str | None:
    if not text:
        return None
    for line in text.splitlines():
        line = line.strip()
        if line:
            return line[:limit]
    return None


def _iso_to_unix(iso: Any) -> float | None:
    if not iso or not isinstance(iso, str):
        return None
    try:
        return dt.datetime.fromisoformat(iso.replace("Z", "+00:00")).timestamp()
    except ValueError:
        return None
