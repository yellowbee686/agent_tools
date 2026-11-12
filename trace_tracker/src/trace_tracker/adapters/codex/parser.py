"""Codex CLI JSONL transcript parser.

Schema (observed from ``~/.codex/sessions/<YYYY>/<MM>/<DD>/rollout-*.jsonl``):

  Top-level record types:
    - ``session_meta``  : id, timestamp, cwd, originator, model_provider,
                          base_instructions, dynamic_tools[]
    - ``turn_context``  : per-turn context incl. model and sandbox policy
    - ``event_msg``     : task_started / token_count / agent_message / ...
    - ``response_item`` : the actual conversation items, with payload.type ∈
                          { message, reasoning, function_call, function_call_output,
                            custom_tool_call, custom_tool_call_output }

  ``response_item.message`` carries role developer/user/assistant and a
  ``content[]`` of ``{type: input_text|output_text, text}``.
  ``reasoning`` items may have server-side ``encrypted_content``, optional
  ``summary[]`` text, or plain ``content[]`` parts such as
  ``{type: reasoning_text, text: ...}``.
"""

from __future__ import annotations

import datetime as dt
import json
from pathlib import Path
from typing import Any

from ...core.models import (
    SessionDetail,
    SessionSummary,
    ToolCall,
    TraceThreadIdentity,
    UnifiedMessage,
)


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------


def parse_thread_identity(path: Path) -> TraceThreadIdentity | None:
    """Read only enough metadata to identify one Codex thread and its session."""
    if not path.is_file():
        return None
    fallback: dict[str, Any] | None = None
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
            if record.get("type") != "session_meta":
                continue
            payload = record.get("payload") or {}
            if not isinstance(payload, dict):
                continue
            fallback = fallback or payload
            if _meta_matches_path(path, payload):
                return _identity_from_meta(path, payload)
    return _identity_from_meta(path, fallback or {})


def parse(path: Path) -> SessionDetail | None:
    """Parse a single rollout-*.jsonl into a fully-populated SessionDetail."""
    if not path.is_file():
        return None

    session_id = path.stem
    messages: list[UnifiedMessage] = []
    meta: dict[str, Any] = {}
    last_turn_context: dict[str, Any] = {}
    last_token_info: dict[str, Any] | None = None

    first_user_text: str | None = None
    first_ts: str | None = None
    last_ts: str | None = None
    api_call_count = 0
    tool_call_count = 0

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
            payload = rec.get("payload") or {}

            if ts:
                if first_ts is None:
                    first_ts = ts
                last_ts = ts

            if t == "session_meta":
                if not meta or _meta_matches_path(path, payload):
                    meta = payload
                if not first_ts:
                    first_ts = payload.get("timestamp")
            elif t == "turn_context":
                last_turn_context = payload
            elif t == "event_msg":
                pt = payload.get("type")
                if pt == "task_started":
                    api_call_count += 1
                elif pt == "token_count":
                    info = payload.get("info")
                    if isinstance(info, dict):
                        last_token_info = info
            elif t == "response_item":
                m = _convert_response_item(payload, len(messages), ts)
                if m is None:
                    continue
                if m.role == "assistant" and m.tool_calls:
                    tool_call_count += len(m.tool_calls)
                if (
                    first_user_text is None
                    and m.role == "user"
                    and m.content
                    and not _is_meta_user_text(m.content)
                ):
                    first_user_text = m.content
                messages.append(m)

    title = _first_line(first_user_text, limit=120) if first_user_text else None

    started_at = _iso_to_unix(meta.get("timestamp")) or _iso_to_unix(first_ts)
    ended_at = _iso_to_unix(last_ts)

    model = (
        last_turn_context.get("model")
        or _stringify(meta.get("model_provider"))
    )

    input_tokens = output_tokens = cache_read_tokens = None
    if last_token_info:
        ttu = last_token_info.get("total_token_usage") or {}
        input_tokens = ttu.get("input_tokens")
        output_tokens = ttu.get("output_tokens")
        cache_read_tokens = ttu.get("cached_input_tokens")

    identity = _identity_from_meta(path, meta)
    summary = SessionSummary(
        agent="codex",
        session_id=session_id,
        title=title,
        model=model,
        started_at=started_at,
        ended_at=ended_at,
        message_count=len(messages),
        tool_call_count=tool_call_count,
        api_call_count=api_call_count,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        cache_read_tokens=cache_read_tokens,
        extras={
            **identity.as_extras(),
            "cwd": meta.get("cwd"),
            "originator": meta.get("originator"),
            "cli_version": meta.get("cli_version"),
            "model_context_window": (last_token_info or {}).get(
                "model_context_window"
            ),
            "reasoning_output_tokens": (
                (last_token_info or {}).get("total_token_usage") or {}
            ).get("reasoning_output_tokens"),
        },
    )

    raw_meta: dict[str, Any] = {}
    raw_meta.update(identity.as_extras())
    for k in ("cwd", "originator", "source", "cli_version"):
        v = meta.get(k)
        if v:
            raw_meta[k] = v
    if last_turn_context.get("personality"):
        raw_meta["personality"] = last_turn_context["personality"]
    if last_turn_context.get("approval_policy"):
        raw_meta["approval_policy"] = last_turn_context["approval_policy"]
    if (last_token_info or {}).get("model_context_window"):
        raw_meta["context_window"] = last_token_info["model_context_window"]

    base_instructions = meta.get("base_instructions") or {}
    system_prompt = ""
    if isinstance(base_instructions, dict):
        system_prompt = base_instructions.get("text") or ""
    elif isinstance(base_instructions, str):
        system_prompt = base_instructions

    tools_def = meta.get("dynamic_tools") or []
    # Normalize so render.py can treat them like OpenAI-style tool defs.
    tools_def = [_normalize_codex_tool(t) for t in tools_def if isinstance(t, dict)]

    return SessionDetail(
        summary=summary,
        messages=messages,
        system_prompt=system_prompt,
        tools_def=tools_def,
        raw_meta=raw_meta,
        raw_path=str(path),
    )


def _meta_matches_path(path: Path, payload: dict[str, Any]) -> bool:
    thread_id = payload.get("id")
    return isinstance(thread_id, str) and path.stem.endswith(thread_id)


def _identity_from_meta(
    path: Path, payload: dict[str, Any]
) -> TraceThreadIdentity:
    source = payload.get("source")
    spawn: dict[str, Any] = {}
    if isinstance(source, dict):
        subagent = source.get("subagent")
        if isinstance(subagent, dict):
            raw_spawn = subagent.get("thread_spawn")
            if isinstance(raw_spawn, dict):
                spawn = raw_spawn

    thread_id = str(payload.get("id") or path.stem)
    session_id = str(payload.get("session_id") or thread_id)
    parent_thread_id = payload.get("parent_thread_id") or spawn.get(
        "parent_thread_id"
    )
    thread_source = payload.get("thread_source")
    is_subagent = thread_source == "subagent" or bool(parent_thread_id)
    raw_depth = spawn.get("depth")
    try:
        depth = int(raw_depth) if raw_depth is not None else (1 if is_subagent else 0)
    except (TypeError, ValueError):
        depth = 1 if is_subagent else 0
    agent_label = payload.get("agent_nickname") or spawn.get("agent_nickname")
    agent_path = payload.get("agent_path") or spawn.get("agent_path")
    return TraceThreadIdentity(
        trace_id=path.stem,
        session_id=session_id,
        thread_id=thread_id,
        parent_thread_id=(str(parent_thread_id) if parent_thread_id else None),
        kind="subagent" if is_subagent else "main",
        depth=depth,
        agent_label=str(agent_label) if agent_label else None,
        agent_path=str(agent_path) if agent_path else None,
    )


# ---------------------------------------------------------------------------
# Internals
# ---------------------------------------------------------------------------


def _convert_response_item(
    p: dict[str, Any], idx: int, ts: str | None
) -> UnifiedMessage | None:
    pt = p.get("type")
    if pt == "message":
        role = p.get("role") or "user"
        # Codex's "developer" role is structurally a system instruction; map
        # it to the "system" badge color but keep the source role in extras.
        display_role = "system" if role == "developer" else role
        return UnifiedMessage(
            index=idx,
            role=display_role,
            content=_flatten_content(p.get("content")),
            timestamp=ts,
            extras={"codex_role": role, "phase": p.get("phase")},
        )
    if pt == "reasoning":
        text = _flatten_reasoning_payload(p)
        encrypted = bool(p.get("encrypted_content")) and not text
        if encrypted:
            text = "(reasoning content is encrypted server-side)"
        return UnifiedMessage(
            index=idx,
            role="reasoning",
            content="",
            reasoning=text,
            timestamp=ts,
            extras={"encrypted": encrypted},
        )
    if pt in {"function_call", "custom_tool_call"}:
        return UnifiedMessage(
            index=idx,
            role="assistant",
            content="",
            tool_calls=[
                ToolCall(
                    id=str(p.get("call_id") or ""),
                    name=(f"{p['namespace']}." if p.get("namespace") else "") + str(p.get("name") or ""),
                    arguments=(p.get("input") if pt == "custom_tool_call" else p.get("arguments")) or "",
                )
            ],
            timestamp=ts,
        )
    if pt in {"function_call_output", "custom_tool_call_output"}:
        out = p.get("output")
        if isinstance(out, dict):
            content_str = (
                out.get("content")
                if isinstance(out.get("content"), str)
                else json.dumps(out, ensure_ascii=False, indent=2)
            )
        elif isinstance(out, list):
            content_str = _flatten_content(out)
        else:
            content_str = "" if out is None else str(out)
        return UnifiedMessage(
            index=idx,
            role="tool",
            content=content_str,
            tool_call_id=p.get("call_id"),
            timestamp=ts,
        )
    return None


def _flatten_reasoning_payload(payload: dict[str, Any]) -> str:
    """Flatten Codex reasoning summary/content variants into display text."""
    parts: list[str] = []
    for value in (payload.get("summary"), payload.get("content")):
        text = _flatten_reasoning_parts(value)
        if text and text not in parts:
            parts.append(text)
    return "\n\n".join(parts).strip()


def _flatten_reasoning_parts(value: Any) -> str:
    """Flatten reasoning content lists, including StepFun reasoning_text parts."""
    if not value:
        return ""
    if isinstance(value, str):
        return value
    if isinstance(value, list):
        parts: list[str] = []
        for item in value:
            text = _reasoning_part_text(item)
            if text:
                parts.append(text)
        return "\n\n".join(parts)
    text = _reasoning_part_text(value)
    return text or json.dumps(value, ensure_ascii=False, indent=2)


def _reasoning_part_text(item: Any) -> str:
    """Extract printable text from one reasoning part."""
    if item is None:
        return ""
    if isinstance(item, str):
        return item
    if not isinstance(item, dict):
        return str(item)

    text = item.get("text")
    if isinstance(text, str):
        return text

    if item.get("type") in {"reasoning_text", "summary_text"}:
        return ""

    return json.dumps(item, ensure_ascii=False, indent=2)


def _flatten_content(content: Any) -> str:
    if not content:
        return ""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for it in content:
            if isinstance(it, dict):
                t = it.get("type")
                if t in ("input_text", "output_text", "text"):
                    parts.append(it.get("text") or "")
                elif t == "input_image":
                    parts.append(f"[image] {it.get('image_url', '')[:200]}")
                else:
                    parts.append(json.dumps(it, ensure_ascii=False, indent=2))
            else:
                parts.append(str(it))
        return "\n".join(parts)
    return json.dumps(content, ensure_ascii=False, indent=2)


def _normalize_codex_tool(t: dict[str, Any]) -> dict[str, Any]:
    """Best-effort normalize a codex dynamic_tool into OpenAI tool def shape."""
    if "function" in t and isinstance(t["function"], dict):
        return t
    name = t.get("name") or t.get("namespace") or "?"
    return {
        "type": "function",
        "function": {
            "name": name,
            "description": t.get("description"),
            "parameters": t.get("parameters") or t.get("input_schema") or {},
        },
    }


def _first_line(text: str | None, limit: int = 120) -> str | None:
    if not text:
        return None
    for line in text.splitlines():
        line = line.strip()
        if line:
            return line[:limit]
    return None


def _is_meta_user_text(text: str) -> bool:
    """Skip auto-injected user messages (cwd dump, AGENTS.md preamble)."""
    head = (text or "").lstrip()[:80].lower()
    if head.startswith("<environment_context>"):
        return True
    if head.startswith("# agents.md"):
        return True
    if head.startswith("<permissions instructions>"):
        return True
    if "agents.md instructions for" in head:
        return True
    return False


def _iso_to_unix(iso: Any) -> float | None:
    if not iso or not isinstance(iso, str):
        return None
    try:
        return dt.datetime.fromisoformat(iso.replace("Z", "+00:00")).timestamp()
    except ValueError:
        return None


def _stringify(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, str):
        return value
    return json.dumps(value, ensure_ascii=False)
