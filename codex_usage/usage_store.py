from __future__ import annotations

import json
import os
import tempfile
from datetime import date, datetime, timezone
from pathlib import Path


DATA_DIR = Path(__file__).with_name("data")
SOURCES = {"app_server", "detailed"}
TOKEN_FIELDS = {
    "cachedTextInputTokens": "cached_text_input_tokens",
    "uncachedTextInputTokens": "uncached_text_input_tokens",
    "textOutputTokens": "text_output_tokens",
    "textTotalTokens": "text_total_tokens",
}


class SnapshotError(ValueError):
    """A malformed or unsupported usage snapshot."""


def _rows_from_payload(payload: object) -> tuple[list[object], str | None]:
    if isinstance(payload, list):
        return payload, None
    if not isinstance(payload, dict):
        raise SnapshotError("detailed response must be a JSON object or array")
    rows = payload.get("data")
    if not isinstance(rows, list):
        raise SnapshotError("detailed response field 'data' must be a list")
    group_by = payload.get("group_by")
    if group_by is not None and not isinstance(group_by, str):
        raise SnapshotError("detailed response field 'group_by' must be a string")
    return rows, group_by


def _token_value(totals: dict[str, object], field: str) -> int:
    value = totals.get(field)
    if value is None:
        return 0
    if not isinstance(value, int) or isinstance(value, bool) or value < 0:
        raise SnapshotError(f"detailed token field {field!r} must be a non-negative integer")
    return value


def _count_value(raw: dict[str, object], field: str, index: int, model_index: int) -> int:
    value = raw.get(field)
    if value is None:
        return 0
    if not isinstance(value, int) or isinstance(value, bool) or value < 0:
        raise SnapshotError(
            f"detailed data row {index} model {model_index} field {field!r} "
            "must be a non-negative integer"
        )
    return value


def _normalize_models(raw_row: dict[str, object], index: int) -> list[dict[str, object]]:
    models = raw_row.get("models")
    if models is None:
        return []
    if not isinstance(models, list):
        raise SnapshotError(f"detailed data row {index} field 'models' must be a list")
    normalized: list[dict[str, object]] = []
    for model_index, raw_model in enumerate(models):
        if not isinstance(raw_model, dict):
            raise SnapshotError(f"detailed data row {index} model {model_index} must be an object")
        name = raw_model.get("model")
        if not isinstance(name, str) or name.strip() == "":
            raise SnapshotError(f"detailed data row {index} model {model_index} has no model id")
        normalized.append(
            {
                "model": name,
                "turns": _count_value(raw_model, "turns", index, model_index),
                "threads": _count_value(raw_model, "threads", index, model_index),
            }
        )
    return normalized


def _merge_models(
    existing: object, incoming: list[dict[str, object]]
) -> list[dict[str, object]]:
    if not isinstance(existing, list):
        raise SnapshotError("normalized detailed models must be a list")
    by_name: dict[str, dict[str, object]] = {}
    for entry in [*existing, *incoming]:
        if not isinstance(entry, dict):
            raise SnapshotError("normalized model entry is invalid")
        name = entry.get("model")
        if not isinstance(name, str):
            raise SnapshotError("normalized model id is invalid")
        current = by_name.setdefault(name, {"model": name, "turns": 0, "threads": 0})
        current["turns"] = int(current["turns"]) + int(entry["turns"])
        current["threads"] = int(current["threads"]) + int(entry["threads"])
    return list(by_name.values())


def normalize_detailed_usage(payload: object) -> dict[str, object]:
    rows, group_by = _rows_from_payload(payload)
    by_date: dict[str, dict[str, object]] = {}
    for index, raw_row in enumerate(rows):
        if not isinstance(raw_row, dict):
            raise SnapshotError(f"detailed data row {index} must be an object")
        row_date = raw_row.get("date")
        if not isinstance(row_date, str):
            raise SnapshotError(f"detailed data row {index} has no date")
        try:
            date.fromisoformat(row_date)
        except ValueError:
            raise SnapshotError(f"detailed data row {index} has invalid date {row_date!r}") from None

        raw_totals = raw_row.get("totals", raw_row)
        if not isinstance(raw_totals, dict):
            raise SnapshotError(f"detailed data row {index} totals must be an object")
        values = {
            normalized: _token_value(raw_totals, source)
            for normalized, source in TOKEN_FIELDS.items()
        }
        components = (
            values["cachedTextInputTokens"]
            + values["uncachedTextInputTokens"]
            + values["textOutputTokens"]
        )
        if "text_total_tokens" not in raw_totals or raw_totals.get("text_total_tokens") is None:
            values["textTotalTokens"] = components

        bucket = by_date.setdefault(
            row_date,
            {
                "startDate": row_date,
                "cachedTextInputTokens": 0,
                "uncachedTextInputTokens": 0,
                "textOutputTokens": 0,
                "textTotalTokens": 0,
                "componentDelta": 0,
                "models": [],
            },
        )
        for field, value in values.items():
            current = bucket[field]
            if not isinstance(current, int):
                raise SnapshotError("normalized detailed bucket is invalid")
            bucket[field] = current + value
        bucket["componentDelta"] = (
            int(bucket["textTotalTokens"])
            - int(bucket["cachedTextInputTokens"])
            - int(bucket["uncachedTextInputTokens"])
            - int(bucket["textOutputTokens"])
        )
        bucket["models"] = _merge_models(bucket["models"], _normalize_models(raw_row, index))

    buckets = [by_date[key] for key in sorted(by_date)]
    totals = {
        field: sum(int(bucket[field]) for bucket in buckets)
        for field in (
            "cachedTextInputTokens",
            "uncachedTextInputTokens",
            "textOutputTokens",
            "textTotalTokens",
            "componentDelta",
        )
    }
    return {
        "groupBy": group_by,
        "dailyUsageBuckets": buckets,
        "totals": totals,
    }


def _snapshot_path(source: str) -> Path:
    if source not in SOURCES:
        raise SnapshotError(f"unsupported snapshot source {source!r}")
    return DATA_DIR / f"{source}.json"


def save_snapshot(source: str, payload: object) -> dict[str, object]:
    path = _snapshot_path(source)
    snapshot = {
        "source": source,
        "capturedAt": datetime.now(timezone.utc).isoformat(),
        "payload": payload,
    }
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    temporary_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=DATA_DIR,
            prefix=f".{source}-",
            suffix=".tmp",
            delete=False,
        ) as temporary:
            json.dump(snapshot, temporary, ensure_ascii=False, indent=2)
            temporary.write("\n")
            temporary.flush()
            os.fsync(temporary.fileno())
            temporary_path = Path(temporary.name)
        temporary_path.chmod(0o600)
        os.replace(temporary_path, path)
    except OSError as exc:
        if temporary_path is not None:
            temporary_path.unlink(missing_ok=True)
        raise SnapshotError(f"could not save {source} snapshot: {exc}") from None
    return snapshot


def load_snapshot(source: str) -> dict[str, object] | None:
    path = _snapshot_path(source)
    if not path.exists():
        return None
    try:
        snapshot = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise SnapshotError(f"could not read {source} snapshot: {exc}") from None
    if not isinstance(snapshot, dict) or snapshot.get("source") != source:
        raise SnapshotError(f"saved {source} snapshot is invalid")
    return snapshot


def compare_sources(
    app_server: dict[str, object], detailed: dict[str, object]
) -> dict[str, object]:
    app_rows = app_server.get("dailyUsageBuckets")
    detail_rows = detailed.get("dailyUsageBuckets")
    if not isinstance(app_rows, list) or not isinstance(detail_rows, list):
        raise SnapshotError("both sources must contain dailyUsageBuckets lists")

    app_by_date = {
        row["startDate"]: row["tokens"]
        for row in app_rows
        if isinstance(row, dict)
        and isinstance(row.get("startDate"), str)
        and isinstance(row.get("tokens"), int)
    }
    detail_by_date = {
        row["startDate"]: row["textTotalTokens"]
        for row in detail_rows
        if isinstance(row, dict)
        and isinstance(row.get("startDate"), str)
        and isinstance(row.get("textTotalTokens"), int)
    }

    daily: list[dict[str, object]] = []
    exact_days = 0
    close_days = 0
    mismatch_days = 0
    app_only_days = 0
    detail_only_days = 0
    for row_date in sorted(set(app_by_date) | set(detail_by_date)):
        app_tokens = app_by_date.get(row_date)
        detailed_tokens = detail_by_date.get(row_date)
        difference: int | None = None
        difference_percent: float | None = None
        if app_tokens is None:
            status = "detailed_only"
            detail_only_days += 1
        elif detailed_tokens is None:
            status = "app_server_only"
            app_only_days += 1
        else:
            difference = detailed_tokens - app_tokens
            difference_percent = (
                abs(difference) / app_tokens * 100 if app_tokens else (0.0 if difference == 0 else 100.0)
            )
            if difference == 0:
                status = "exact"
                exact_days += 1
            elif difference_percent <= 1:
                status = "close"
                close_days += 1
            else:
                status = "mismatch"
                mismatch_days += 1
        daily.append(
            {
                "startDate": row_date,
                "appServerTokens": app_tokens,
                "detailedTokens": detailed_tokens,
                "differenceTokens": difference,
                "differencePercent": difference_percent,
                "status": status,
            }
        )

    matched_days = exact_days + close_days + mismatch_days
    return {
        "summary": {
            "matchedDays": matched_days,
            "exactDays": exact_days,
            "closeDays": close_days,
            "mismatchDays": mismatch_days,
            "appServerOnlyDays": app_only_days,
            "detailedOnlyDays": detail_only_days,
        },
        "daily": daily,
    }
