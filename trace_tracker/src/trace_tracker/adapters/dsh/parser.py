"""Parse native DeepSeek Harness plain and zstd-compressed JSONL traces."""

from __future__ import annotations

import io
import json
import logging
from collections.abc import Iterator
from contextlib import closing
from pathlib import Path
from typing import Any

import zstandard

from ...core.models import SessionDetail, SessionSummary, ToolCall, UnifiedMessage

LOGGER = logging.getLogger(__name__)


def _trace_lines(path: Path) -> Iterator[str]:
    """Read complete lines across appended zstd frames, including live traces."""
    try:
        with path.open("rb") as source:
            stream = (zstandard.ZstdDecompressor().stream_reader(source, read_across_frames=True)
                      if path.suffix in {".zstd", ".zst"} else source)
            with io.TextIOWrapper(stream, encoding="utf-8", errors="replace") as handle:
                for line in handle:
                    # A writer may still be appending the final record/frame.
                    if path.suffix not in {".zstd", ".zst"} or line.endswith("\n"):
                        yield line
    except (OSError, zstandard.ZstdError) as error:
        LOGGER.error("Unable to read DSH trace %s: %s", path, error)


def _records(handle: Iterator[str], session_id: str | None) -> Iterator[dict[str, Any] | None]:
    selected = session_id
    for line in handle:
        if not line.strip():
            continue
        try:
            record = json.loads(line)
        except json.JSONDecodeError:
            yield None
            continue
        if not isinstance(record, dict):
            continue
        if record.get("method") == "session.event":
            params = record.get("params") or {}
            if selected is None and not params.get("parentSessionId"):
                selected = params.get("sessionId")
            if not selected or params.get("sessionId") != selected:
                continue
            event = params.get("event")
            if isinstance(event, dict):
                yield {**event, "_session_id": selected}
        elif "type" in record:
            yield record


def session_header(path: Path, session_id: str | None = None) -> dict[str, Any]:
    """Return the leading DSH session record without parsing the transcript."""
    if not path.is_file():
        return {}
    try:
        with closing(_trace_lines(path)) as handle:
            for index, record in enumerate(_records(handle, session_id)):
                if index >= 20:
                    break
                if record is None:
                    continue
                if record.get("type") == "session":
                    return record
                if record.get("_session_id"):
                    return {"type": "session", "id": record["_session_id"], "createdAt": record.get("time")}
    except OSError:
        return {}
    return {}


def parse(path: Path, *, session_id: str | None = None) -> SessionDetail | None:
    """Parse one DSH session JSONL file into the shared trace model."""
    header = session_header(path, session_id)
    if not header:
        return None

    messages: list[UnifiedMessage] = []
    tool_names: dict[str, str] = {}
    title: str | None = None
    model: str | None = None
    system_prompt = ""
    tools_def: list[dict[str, Any]] = []
    ended_at = _timestamp(header.get("createdAt"))
    end_reason: str | None = None
    input_tokens = 0
    output_tokens = 0
    cache_read_tokens = 0
    api_call_count = 0
    tool_call_count = 0
    parse_errors = 0

    with closing(_trace_lines(path)) as handle:
        for record in _records(handle, session_id):
            if record is None:
                parse_errors += 1
                continue

            event_type = str(record.get("type") or "")
            timestamp = _timestamp(record.get("time"))
            if timestamp is not None:
                ended_at = timestamp
            data = record.get("data")
            if not isinstance(data, dict):
                data = {}

            if event_type == "session/title":
                value = data.get("title")
                if isinstance(value, str) and value.strip():
                    title = value.strip()
                continue

            if event_type == "request/header":
                request_header = data.get("header")
                if isinstance(request_header, dict):
                    system_prompt = _string_value(request_header.get("system"))
                    tools_def = _tool_definitions(request_header.get("tools"))
                    config = request_header.get("config")
                    if isinstance(config, dict) and config.get("model"):
                        model = str(config["model"])
                continue

            if event_type == "request/context":
                if data.get("model"):
                    model = str(data["model"])
                continue

            if event_type == "tool/call":
                call_id = str(data.get("callId") or "")
                name = str(data.get("name") or "")
                if call_id:
                    tool_names[call_id] = name
                continue

            if event_type in {"user/message", "system/message"}:
                source = data.get("source")
                role = "system" if event_type == "system/message" or _is_instruction_source(source) else "user"
                messages.append(
                    UnifiedMessage(
                        index=len(messages),
                        role=role,
                        content=_render_blocks(data.get("content")),
                        timestamp=timestamp,
                        extras=_event_extras(event_type, data, source),
                    )
                )
                continue

            if event_type == "assistant/message":
                payload = data.get("message")
                if not isinstance(payload, dict):
                    payload = data
                blocks = payload.get("content")
                if not isinstance(blocks, list):
                    blocks = []
                reasoning = "\n".join(
                    str(block.get("text"))
                    for block in blocks
                    if isinstance(block, dict)
                    and block.get("type") == "reasoning"
                    and block.get("text")
                )
                content = "\n".join(
                    str(block.get("text"))
                    for block in blocks
                    if isinstance(block, dict)
                    and block.get("type") == "text"
                    and block.get("text")
                )
                calls = [
                    ToolCall(
                        id=str(block.get("id") or ""),
                        name=str(block.get("name") or ""),
                        arguments=block.get("arguments", ""),
                    )
                    for block in blocks
                    if isinstance(block, dict) and block.get("type") == "tool-call"
                ]
                for call in calls:
                    if call.id:
                        tool_names[call.id] = call.name
                source = payload.get("source") or payload.get("provenance")
                if isinstance(source, dict) and source.get("model"):
                    model = str(source["model"])
                usage = data.get("usage") or payload.get("usage")
                if isinstance(usage, dict):
                    input_tokens += _integer(usage.get("inputTokens"))
                    output_tokens += _integer(usage.get("outputTokens"))
                    cache_read_tokens += _integer(usage.get("cacheReadTokens"))
                api_call_count += 1
                tool_call_count += len(calls)
                messages.append(
                    UnifiedMessage(
                        index=len(messages),
                        role="assistant",
                        content=content,
                        reasoning=reasoning,
                        tool_calls=calls,
                        timestamp=timestamp,
                        extras=_event_extras(event_type, data, source),
                    )
                )
                continue

            if event_type == "tool/result":
                payload = data.get("message")
                if not isinstance(payload, dict):
                    payload = data
                source = payload.get("source")
                call_id = (
                    str(payload.get("toolCallId") or source.get("callId") or "")
                    if isinstance(source, dict)
                    else str(payload.get("toolCallId") or "")
                )
                messages.append(
                    UnifiedMessage(
                        index=len(messages),
                        role="tool",
                        content=_render_blocks(payload.get("content")),
                        tool_name=tool_names.get(call_id) or None,
                        tool_call_id=call_id or None,
                        timestamp=timestamp,
                        extras=_event_extras(event_type, data, source),
                    )
                )
                continue

            if event_type == "turn/end":
                reason = data.get("reason")
                if isinstance(reason, dict) and reason.get("kind"):
                    end_reason = str(reason["kind"])

    first_user = next(
        (
            message.content
            for message in messages
            if message.role == "user" and message.content
        ),
        None,
    )
    if title is None and first_user:
        title = first_user.splitlines()[0][:120]

    session_id = str(header.get("id") or path.stem)
    cwd = str(header.get("cwd") or "")
    started_at = _timestamp(header.get("createdAt"))
    raw_meta: dict[str, Any] = {
        "cwd": cwd,
        "version": header.get("version"),
        "delegationDepth": header.get("delegationDepth"),
    }
    if parse_errors:
        raw_meta["parse_errors"] = parse_errors

    return SessionDetail(
        summary=SessionSummary(
            agent="dsh",
            session_id=session_id,
            title=title,
            model=model,
            started_at=started_at,
            ended_at=ended_at,
            message_count=len(messages),
            tool_call_count=tool_call_count,
            api_call_count=api_call_count,
            input_tokens=input_tokens or None,
            output_tokens=output_tokens or None,
            cache_read_tokens=cache_read_tokens or None,
            end_reason=end_reason,
            extras={"cwd": cwd},
        ),
        messages=messages,
        system_prompt=system_prompt,
        tools_def=tools_def,
        raw_meta=raw_meta,
        raw_path=str(path),
        error=(
            f"Skipped {parse_errors} malformed JSONL line(s)."
            if parse_errors
            else None
        ),
    )


def _event_extras(
    event_type: str,
    data: dict[str, Any],
    source: Any,
) -> dict[str, Any]:
    result: dict[str, Any] = {"dsh_event_type": event_type}
    for key in ("turn", "step"):
        if key in data:
            result[key] = data[key]
    if isinstance(source, dict):
        result["dsh_source"] = source
    return result


def _is_instruction_source(source: Any) -> bool:
    if not isinstance(source, dict):
        return False
    if source.get("kind") == "agent-instructions":
        return True
    return source.get("kind") == "plugin" and source.get("form") in {
        "catalog",
        "instructions",
        "snapshot",
    }


def _render_blocks(value: Any) -> str:
    if isinstance(value, str):
        return value
    if not isinstance(value, list):
        return ""
    rendered: list[str] = []
    for block in value:
        if not isinstance(block, dict):
            continue
        block_type = block.get("type")
        if block_type in {"text", "reasoning"} and block.get("text"):
            rendered.append(str(block["text"]))
        elif block_type == "tool-result":
            nested = _render_blocks(block.get("content"))
            if nested:
                rendered.append(nested)
    return "\n".join(rendered)


def _tool_definitions(value: Any) -> list[dict[str, Any]]:
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except json.JSONDecodeError:
            return []
    if isinstance(value, list):
        return [item for item in value if isinstance(item, dict)]
    return []


def _string_value(value: Any) -> str:
    if isinstance(value, str):
        return value
    if value is None:
        return ""
    return json.dumps(value, ensure_ascii=False, indent=2)


def _timestamp(value: Any) -> float | None:
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        return None
    numeric = float(value)
    return numeric / 1000 if numeric > 10_000_000_000 else numeric


def _integer(value: Any) -> int:
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return int(value)
    return 0
