"""Read native DSH assistant frames captured by the SDK transport, without running it."""

from __future__ import annotations

import json
import threading
from pathlib import Path
from typing import Any

from ...core.models import ToolCall, UnifiedMessage


class LiveTraceReader:
    """Incrementally fold the current attempt of each session in one process log."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self._offset = 0
        self._inode: int | None = None
        self._attempts: dict[str, dict[str, Any]] = {}
        self._lock = threading.Lock()

    def message(self, session_id: str) -> UnifiedMessage | None:
        with self._lock:
            if not self.path.is_file():
                return None
            stat = self.path.stat()
            if stat.st_ino != self._inode or stat.st_size < self._offset:
                self._offset = 0
                self._attempts.clear()
                self._inode = stat.st_ino
            with self.path.open("rb") as handle:
                handle.seek(self._offset)
                while line := handle.readline():
                    if not line.endswith(b"\n"):
                        break
                    self._offset = handle.tell()
                    try:
                        record = json.loads(line)
                    except (json.JSONDecodeError, UnicodeDecodeError):
                        continue
                    if record.get("method") == "session.stream":
                        self._apply(record["params"])
            attempt = self._attempts.get(session_id)
            if attempt is None:
                return None
            blocks = list(attempt["blocks"].values())
            return UnifiedMessage(
                role="assistant", timestamp=attempt["time"] / 1000,
                content="".join(b["text"] for b in blocks if b["type"] == "text-delta"),
                reasoning="".join(b["text"] for b in blocks if b["type"] == "reasoning-delta"),
                tool_calls=[ToolCall(id=b["id"], name=b["name"], arguments=b["text"])
                            for b in blocks if b["type"] == "tool-call-delta"],
                extras={"streaming": True, "attempt_id": attempt["attemptId"],
                        "turn": attempt["turn"], "step": attempt["step"]},
            )

    def _apply(self, params: dict[str, Any]) -> None:
        session_id, frame = params["sessionId"], params["frame"]
        kind = frame["type"]
        if kind == "start":
            self._attempts[session_id] = {**frame, "time": params["time"], "blocks": {}}
            return
        attempt = self._attempts.get(session_id)
        if attempt is None or attempt["attemptId"] != frame["attemptId"]:
            return
        if kind == "end":
            outcome = frame["outcome"]
            if outcome.get("eventType") != "assistant/message":
                self._attempts.pop(session_id, None)
            return
        chunk = frame.get("chunk", {})
        if chunk.get("type") not in {"text-delta", "reasoning-delta", "tool-call-delta"}:
            return
        key = (chunk["type"], chunk["index"])
        block = attempt["blocks"].setdefault(key, {"type": chunk["type"], "text": "", "id": "", "name": ""})
        block["text"] += chunk.get("argumentsDelta", "") if chunk["type"] == "tool-call-delta" else chunk["text"]
        if chunk.get("id"):
            block["id"] = chunk["id"]
        if chunk.get("name"):
            block["name"] = chunk["name"]
