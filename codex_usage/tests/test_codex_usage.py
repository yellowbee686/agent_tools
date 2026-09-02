from __future__ import annotations

import csv
import io
import json
import os
import unittest
from contextlib import redirect_stdout
from datetime import date
from unittest.mock import patch

import codex_usage


class RecordingStdin(io.StringIO):
    def close(self) -> None:
        self.flush()


class FakeProcess:
    def __init__(self, stdout: str) -> None:
        self.stdin = RecordingStdin()
        self.stdout = io.StringIO(stdout)
        self.stderr = io.StringIO("")
        self.returncode = 0
        self.terminated = False

    def wait(self, timeout: int) -> int:
        return self.returncode

    def terminate(self) -> None:
        self.terminated = True


class AppServerTest(unittest.TestCase):
    @patch("codex_usage.subprocess.Popen")
    def test_reads_personal_account_usage_from_codex_app_server(
        self, popen: unittest.mock.Mock
    ) -> None:
        process = FakeProcess(
                '{"id":0,"result":{"userAgent":"Codex"}}\n'
                '{"method":"account/updated","params":{"authMode":"chatgpt"}}\n'
                '{"id":1,"result":{"summary":{"lifetimeTokens":123},'
                '"dailyUsageBuckets":[{"startDate":"2026-08-23","tokens":42}]}}\n'
        )
        popen.return_value = process

        usage = codex_usage.read_account_usage()

        self.assertEqual(usage["summary"]["lifetimeTokens"], 123)
        self.assertEqual(usage["dailyUsageBuckets"][0]["tokens"], 42)
        command = popen.call_args.args[0]
        request_body = process.stdin.getvalue()
        self.assertEqual(command, ["codex", "app-server", "--stdio"])
        self.assertIn('"method": "initialize"', request_body)
        self.assertIn('"method": "initialized"', request_body)
        self.assertIn('"method": "account/usage/read"', request_body)
        self.assertNotIn("API_KEY", request_body)

    @patch("codex_usage.subprocess.Popen")
    def test_surfaces_app_server_auth_error(self, popen: unittest.mock.Mock) -> None:
        popen.return_value = FakeProcess(
                '{"id":0,"result":{"userAgent":"Codex"}}\n'
                '{"id":1,"error":{"code":-32001,"message":"Login required"}}\n'
        )

        with self.assertRaisesRegex(codex_usage.UsageError, "Login required"):
            codex_usage.read_account_usage()


class WindowTest(unittest.TestCase):
    def setUp(self) -> None:
        self.usage: dict[str, object] = {
            "summary": {
                "lifetimeTokens": 1000,
                "peakDailyTokens": 300,
                "longestRunningTurnSec": 60,
                "currentStreakDays": 2,
                "longestStreakDays": 4,
            },
            "dailyUsageBuckets": [
                {"startDate": "2026-07-24", "tokens": 5},
                {"startDate": "2026-07-25", "tokens": 100},
                {"startDate": "2026-08-01", "tokens": 200},
                {"startDate": "2026-08-23", "tokens": 300},
                {"startDate": "2026-08-24", "tokens": 400},
            ],
        }

    def test_default_month_is_last_30_complete_utc_days(self) -> None:
        report = codex_usage.build_report(
            self.usage,
            today=date(2026, 8, 24),
            start_date=None,
            end_date=None,
        )

        self.assertEqual(report["period"]["startDate"], "2026-07-25")
        self.assertEqual(report["period"]["endDateExclusive"], "2026-08-24")
        self.assertEqual(report["period"]["totalTokens"], 600)
        self.assertEqual(report["period"]["activeDays"], 3)
        self.assertEqual(
            [bucket["startDate"] for bucket in report["dailyUsageBuckets"]],
            ["2026-07-25", "2026-08-01", "2026-08-23"],
        )

    def test_explicit_range_uses_exclusive_end(self) -> None:
        report = codex_usage.build_report(
            self.usage,
            today=date(2026, 8, 24),
            start_date="2026-08-01",
            end_date="2026-08-24",
        )

        self.assertEqual(report["period"]["totalTokens"], 500)

    def test_requires_both_explicit_dates(self) -> None:
        with self.assertRaisesRegex(ValueError, "together"):
            codex_usage.build_report(
                self.usage,
                today=date(2026, 8, 24),
                start_date="2026-08-01",
                end_date=None,
            )


class ReportingTest(unittest.TestCase):
    def setUp(self) -> None:
        self.report: dict[str, object] = {
            "period": {
                "startDate": "2026-07-25",
                "endDateExclusive": "2026-08-24",
                "totalTokens": 600,
                "activeDays": 2,
            },
            "accountSummary": {"lifetimeTokens": 1000},
            "dailyUsageBuckets": [
                {"startDate": "2026-07-25", "tokens": 100},
                {"startDate": "2026-08-23", "tokens": 500},
            ],
        }

    def test_table_includes_days_and_total(self) -> None:
        rendered = codex_usage.render_table(self.report)

        self.assertIn("2026-07-25", rendered)
        self.assertIn("500", rendered)
        self.assertIn("TOTAL", rendered)
        self.assertIn("600", rendered)

    def test_json_preserves_account_summary(self) -> None:
        payload = json.loads(codex_usage.render_json(self.report))

        self.assertEqual(payload["accountSummary"]["lifetimeTokens"], 1000)
        self.assertEqual(payload["period"]["totalTokens"], 600)

    def test_csv_contains_daily_buckets(self) -> None:
        records = list(csv.DictReader(io.StringIO(codex_usage.render_csv(self.report))))

        self.assertEqual(records[0], {"date": "2026-07-25", "tokens": "100"})
        self.assertEqual(records[1], {"date": "2026-08-23", "tokens": "500"})


class CliTest(unittest.TestCase):
    @patch.dict(os.environ, {}, clear=True)
    @patch("codex_usage.utc_today", return_value=date(2026, 8, 24))
    @patch("codex_usage.read_account_usage")
    def test_month_uses_current_codex_login_without_credentials(
        self,
        read_account_usage: unittest.mock.Mock,
        _utc_today: unittest.mock.Mock,
    ) -> None:
        read_account_usage.return_value = {
            "summary": {"lifetimeTokens": 42},
            "dailyUsageBuckets": [{"startDate": "2026-08-23", "tokens": 42}],
        }

        with redirect_stdout(io.StringIO()) as stdout:
            exit_code = codex_usage.main(["month", "--format", "json"])

        self.assertEqual(exit_code, 0)
        self.assertEqual(json.loads(stdout.getvalue())["period"]["totalTokens"], 42)
        read_account_usage.assert_called_once_with()


if __name__ == "__main__":
    unittest.main()
