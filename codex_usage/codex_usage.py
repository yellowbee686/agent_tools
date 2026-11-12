from __future__ import annotations

import argparse
import csv
import io
import json
import logging
import queue
import subprocess
import sys
import threading
import time
from datetime import date, datetime, timedelta, timezone
from typing import TextIO


DEFAULT_DAYS = 30
APP_SERVER_TIMEOUT_SECONDS = 30

logger = logging.getLogger(__name__)


class UsageError(Exception):
    """A user-actionable Codex account usage failure."""


def _send_message(stream: TextIO, message: dict[str, object]) -> None:
    stream.write(f"{json.dumps(message)}\n")
    stream.flush()


def _pump_stdout(stream: TextIO, lines: queue.Queue[str | None]) -> None:
    for line in stream:
        lines.put(line)
    lines.put(None)


def _wait_for_response(
    lines: queue.Queue[str | None], response_id: int
) -> dict[str, object]:
    deadline = time.monotonic() + APP_SERVER_TIMEOUT_SECONDS
    while True:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise UsageError(
                f"Codex app-server timed out waiting for response id {response_id}"
            )
        try:
            line = lines.get(timeout=remaining)
        except queue.Empty:
            raise UsageError(
                f"Codex app-server timed out waiting for response id {response_id}"
            ) from None
        if line is None:
            raise UsageError(
                f"Codex app-server ended before response id {response_id}"
            )
        try:
            message = json.loads(line)
        except json.JSONDecodeError:
            raise UsageError("Codex app-server returned invalid JSON") from None
        if not isinstance(message, dict):
            raise UsageError("Codex app-server response must be a JSON object")
        if message.get("id") == response_id:
            return message


def _result_or_error(
    responses: dict[int, dict[str, object]], response_id: int
) -> dict[str, object]:
    response = responses.get(response_id)
    if response is None:
        raise UsageError(f"Codex app-server did not return response id {response_id}")
    error = response.get("error")
    if isinstance(error, dict):
        message = error.get("message")
        detail = message if isinstance(message, str) else json.dumps(error)
        raise UsageError(f"Codex app-server error: {detail}")
    result = response.get("result")
    if not isinstance(result, dict):
        raise UsageError(f"Codex app-server response id {response_id} has no result")
    return result


def _validate_usage(usage: dict[str, object]) -> dict[str, object]:
    summary = usage.get("summary")
    if not isinstance(summary, dict):
        raise UsageError("account usage summary must be an object")

    buckets = usage.get("dailyUsageBuckets")
    if buckets is None:
        buckets = []
    if not isinstance(buckets, list):
        raise UsageError("account daily usage buckets must be a list")
    for bucket in buckets:
        if not isinstance(bucket, dict):
            raise UsageError("each daily usage bucket must be an object")
        start_date = bucket.get("startDate")
        tokens = bucket.get("tokens")
        if not isinstance(start_date, str):
            raise UsageError("daily usage bucket startDate must be a string")
        try:
            date.fromisoformat(start_date)
        except ValueError:
            raise UsageError(
                f"daily usage bucket has invalid date {start_date!r}"
            ) from None
        if not isinstance(tokens, int) or isinstance(tokens, bool) or tokens < 0:
            raise UsageError("daily usage bucket tokens must be a non-negative integer")

    return {"summary": summary, "dailyUsageBuckets": buckets}


def read_account_usage() -> dict[str, object]:
    """Read usage through the current local Codex ChatGPT login."""
    try:
        process = subprocess.Popen(
            ["codex", "app-server", "--stdio"],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            bufsize=1,
        )
    except FileNotFoundError:
        raise UsageError("codex CLI was not found; install it and sign in first") from None
    except OSError as exc:
        raise UsageError(f"could not start Codex app-server: {exc}") from None

    if process.stdin is None or process.stdout is None or process.stderr is None:
        process.terminate()
        raise UsageError("could not open Codex app-server stdio streams")

    lines: queue.Queue[str | None] = queue.Queue()
    reader = threading.Thread(
        target=_pump_stdout,
        args=(process.stdout, lines),
        daemon=True,
    )
    reader.start()
    exit_code: int | None = None
    try:
        _send_message(
            process.stdin,
            {
                "method": "initialize",
                "id": 0,
                "params": {
                    "clientInfo": {
                        "name": "codex_usage",
                        "title": "Codex Usage",
                        "version": "0.2.0",
                    }
                },
            },
        )
        initialize_response = _wait_for_response(lines, 0)
        _result_or_error({0: initialize_response}, 0)

        _send_message(process.stdin, {"method": "initialized", "params": {}})
        _send_message(process.stdin, {"method": "account/usage/read", "id": 1})
        usage_response = _wait_for_response(lines, 1)
        usage = _result_or_error({1: usage_response}, 1)
    finally:
        process.stdin.close()
        try:
            exit_code = process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.terminate()
            exit_code = process.wait(timeout=5)
        reader.join(timeout=1)

    if exit_code != 0:
        detail = process.stderr.read().strip()
        suffix = f": {detail[-1000:]}" if detail else ""
        raise UsageError(f"Codex app-server exited with code {exit_code}{suffix}")
    return _validate_usage(usage)


def utc_today() -> date:
    return datetime.now(timezone.utc).date()


def _parse_date(value: str) -> date:
    try:
        return date.fromisoformat(value)
    except ValueError:
        raise ValueError(f"invalid date {value!r}; expected YYYY-MM-DD") from None


def build_report(
    usage: dict[str, object],
    *,
    today: date,
    start_date: str | None,
    end_date: str | None,
) -> dict[str, object]:
    """Select a UTC reporting window and calculate its token total."""
    if (start_date is None) != (end_date is None):
        raise ValueError("--start and --end must be provided together")
    if start_date is None and end_date is None:
        end = today
        start = end - timedelta(days=DEFAULT_DAYS)
    else:
        start = _parse_date(start_date or "")
        end = _parse_date(end_date or "")
    if start >= end:
        raise ValueError("--start must be earlier than --end")

    validated = _validate_usage(usage)
    raw_buckets = validated["dailyUsageBuckets"]
    selected: list[dict[str, object]] = []
    for bucket in raw_buckets:
        bucket_date = _parse_date(bucket["startDate"])
        if start <= bucket_date < end:
            selected.append(bucket)
    selected.sort(key=lambda bucket: bucket["startDate"])

    total_tokens = sum(bucket["tokens"] for bucket in selected)
    return {
        "period": {
            "startDate": start.isoformat(),
            "endDateExclusive": end.isoformat(),
            "totalTokens": total_tokens,
            "activeDays": len(selected),
        },
        "accountSummary": validated["summary"],
        "dailyUsageBuckets": selected,
    }


def _report_buckets(report: dict[str, object]) -> list[dict[str, object]]:
    buckets = report.get("dailyUsageBuckets")
    if not isinstance(buckets, list) or any(
        not isinstance(bucket, dict) for bucket in buckets
    ):
        raise UsageError("report dailyUsageBuckets must be a list of objects")
    return buckets


def _report_period(report: dict[str, object]) -> dict[str, object]:
    period = report.get("period")
    if not isinstance(period, dict):
        raise UsageError("report period must be an object")
    return period


def render_table(report: dict[str, object]) -> str:
    buckets = _report_buckets(report)
    period = _report_period(report)
    rows = [(bucket["startDate"], f"{bucket['tokens']:,}") for bucket in buckets]
    rows.append(("TOTAL", f"{period['totalTokens']:,}"))
    date_width = max(len("Date"), *(len(str(row[0])) for row in rows))
    token_width = max(len("Tokens"), *(len(row[1]) for row in rows))
    separator = f"{'-' * date_width}  {'-' * token_width}"
    rendered = [
        f"{'Date'.ljust(date_width)}  {'Tokens'.rjust(token_width)}",
        separator,
    ]
    for index, (bucket_date, tokens) in enumerate(rows):
        if index == len(rows) - 1:
            rendered.append(separator)
        rendered.append(f"{str(bucket_date).ljust(date_width)}  {tokens.rjust(token_width)}")
    return "\n".join(rendered)


def render_json(report: dict[str, object]) -> str:
    return json.dumps(report, ensure_ascii=False, indent=2)


def render_csv(report: dict[str, object]) -> str:
    output = io.StringIO(newline="")
    writer = csv.DictWriter(output, fieldnames=("date", "tokens"))
    writer.writeheader()
    for bucket in _report_buckets(report):
        writer.writerow({"date": bucket["startDate"], "tokens": bucket["tokens"]})
    return output.getvalue()


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="codex-usage",
        description="Read personal ChatGPT Codex token usage from Codex app-server.",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)
    month = subparsers.add_parser(
        "month",
        help="show the last 30 complete UTC calendar days",
    )
    month.add_argument(
        "--format",
        choices=("table", "json", "csv"),
        default="table",
        help="output format (default: table)",
    )
    month.add_argument(
        "--start",
        help="inclusive UTC start date, YYYY-MM-DD; requires --end",
    )
    month.add_argument(
        "--end",
        help="exclusive UTC end date, YYYY-MM-DD; requires --start",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
    args = _build_parser().parse_args(argv)
    try:
        usage = read_account_usage()
        report = build_report(
            usage,
            today=utc_today(),
            start_date=args.start,
            end_date=args.end,
        )
        renderers = {
            "table": render_table,
            "json": render_json,
            "csv": render_csv,
        }
        print(renderers[args.format](report))
    except (UsageError, ValueError) as exc:
        logger.error("%s", exc)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
