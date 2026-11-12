from __future__ import annotations

import json
import threading
import unittest
from datetime import date
from urllib.request import Request, urlopen
from unittest.mock import patch

import web_server


class UsagePayloadTest(unittest.TestCase):
    @patch("web_server.utc_today", return_value=date(2026, 8, 24))
    @patch("web_server.load_snapshot", return_value=None)
    @patch("web_server.save_snapshot")
    @patch("web_server.read_account_usage")
    def test_builds_browser_payload_for_current_personal_account(
        self,
        read_account_usage: unittest.mock.Mock,
        save_snapshot: unittest.mock.Mock,
        _load_snapshot: unittest.mock.Mock,
        _utc_today: unittest.mock.Mock,
    ) -> None:
        usage = {
            "summary": {"lifetimeTokens": 1000},
            "dailyUsageBuckets": [
                {"startDate": "2026-08-22", "tokens": 100},
                {"startDate": "2026-08-23", "tokens": 200},
            ],
        }
        read_account_usage.return_value = usage
        save_snapshot.return_value = {
            "source": "app_server",
            "capturedAt": "2026-08-24T10:00:00+00:00",
            "payload": usage,
        }

        payload = web_server.build_usage_payload()

        self.assertEqual(payload["accountSummary"]["lifetimeTokens"], 1000)
        self.assertEqual(payload["defaultWindow"]["startDate"], "2026-07-25")
        self.assertEqual(payload["defaultWindow"]["endDateInclusive"], "2026-08-23")
        self.assertEqual(payload["dailyUsageBuckets"][1]["tokens"], 200)
        self.assertIsNone(payload["sources"]["detailed"])


class HttpServerTest(unittest.TestCase):
    @patch("web_server.build_usage_payload")
    def test_serves_dashboard_and_usage_api(
        self, build_usage_payload: unittest.mock.Mock
    ) -> None:
        build_usage_payload.return_value = {
            "accountSummary": {"lifetimeTokens": 42},
            "defaultWindow": {
                "startDate": "2026-07-25",
                "endDateInclusive": "2026-08-23",
            },
            "dailyUsageBuckets": [],
        }
        server = web_server.create_server(port=0)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        port = server.server_address[1]
        try:
            with urlopen(f"http://127.0.0.1:{port}/", timeout=2) as response:
                html = response.read().decode("utf-8")
            with urlopen(f"http://127.0.0.1:{port}/api/usage", timeout=2) as response:
                payload = json.load(response)
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=2)

        self.assertIn("Codex Usage", html)
        self.assertEqual(payload["accountSummary"]["lifetimeTokens"], 42)

    @patch("web_server.import_detailed_payload")
    def test_imports_copied_detailed_response(
        self, import_detailed_payload: unittest.mock.Mock
    ) -> None:
        import_detailed_payload.return_value = {
            "sources": {"appServer": {}, "detailed": {"dailyUsageBuckets": []}},
            "comparison": {"summary": {"matchedDays": 0}, "daily": []},
        }
        server = web_server.create_server(port=0)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        port = server.server_address[1]
        request = Request(
            f"http://127.0.0.1:{port}/api/detailed-usage",
            data=json.dumps({"data": []}).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urlopen(request, timeout=2) as response:
                payload = json.load(response)
                status = response.status
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=2)

        self.assertEqual(status, 201)
        self.assertEqual(payload["comparison"]["summary"]["matchedDays"], 0)
        import_detailed_payload.assert_called_once_with({"data": []})


if __name__ == "__main__":
    unittest.main()
