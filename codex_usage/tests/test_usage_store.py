from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from stat import S_IMODE
from unittest.mock import patch

import usage_store


class DetailedUsageTest(unittest.TestCase):
    def setUp(self) -> None:
        self.payload: dict[str, object] = {
            "group_by": "day",
            "data": [
                {
                    "date": "2026-08-22",
                    "totals": {
                        "cached_text_input_tokens": 60,
                        "uncached_text_input_tokens": 30,
                        "text_output_tokens": 10,
                        "text_total_tokens": 100,
                    },
                },
                {
                    "date": "2026-08-23",
                    "totals": {
                        "cached_text_input_tokens": 120,
                        "uncached_text_input_tokens": 50,
                        "text_output_tokens": 30,
                        "text_total_tokens": 200,
                    },
                },
            ],
        }

    def test_normalizes_detailed_token_breakdown(self) -> None:
        normalized = usage_store.normalize_detailed_usage(self.payload)

        self.assertEqual(normalized["groupBy"], "day")
        self.assertEqual(normalized["dailyUsageBuckets"][0]["startDate"], "2026-08-22")
        self.assertEqual(normalized["dailyUsageBuckets"][1]["cachedTextInputTokens"], 120)
        self.assertEqual(normalized["totals"]["textTotalTokens"], 300)
        self.assertEqual(normalized["totals"]["componentDelta"], 0)
        self.assertEqual(normalized["dailyUsageBuckets"][0]["models"], [])
        self.assertEqual(normalized["dailyUsageBuckets"][1]["models"], [])

    def test_extracts_model_turn_counts(self) -> None:
        payload = {
            "group_by": "day",
            "data": [
                {
                    "date": "2026-09-03",
                    "totals": {
                        "cached_text_input_tokens": 10,
                        "uncached_text_input_tokens": 5,
                        "text_output_tokens": 1,
                        "text_total_tokens": 16,
                    },
                    "models": [
                        {"model": "gpt-5.6-sol", "turns": 23, "threads": 10},
                        {"model": "gpt-6-astra", "turns": 7, "threads": 2},
                    ],
                },
                {
                    "date": "2026-09-09",
                    "totals": {
                        "cached_text_input_tokens": 4,
                        "uncached_text_input_tokens": 2,
                        "text_output_tokens": 1,
                        "text_total_tokens": 7,
                    },
                },
            ],
        }

        normalized = usage_store.normalize_detailed_usage(payload)

        self.assertEqual(
            normalized["dailyUsageBuckets"][0]["models"],
            [
                {"model": "gpt-5.6-sol", "turns": 23, "threads": 10},
                {"model": "gpt-6-astra", "turns": 7, "threads": 2},
            ],
        )
        self.assertEqual(normalized["dailyUsageBuckets"][1]["models"], [])

    def test_rejects_non_list_models(self) -> None:
        payload = {
            "data": [
                {
                    "date": "2026-09-03",
                    "totals": {
                        "cached_text_input_tokens": 1,
                        "uncached_text_input_tokens": 1,
                        "text_output_tokens": 0,
                        "text_total_tokens": 2,
                    },
                    "models": {"model": "gpt-6-astra"},
                }
            ]
        }
        with self.assertRaisesRegex(usage_store.SnapshotError, "models"):
            usage_store.normalize_detailed_usage(payload)

    def test_rejects_missing_daily_rows(self) -> None:
        with self.assertRaisesRegex(usage_store.SnapshotError, "data"):
            usage_store.normalize_detailed_usage({"group_by": "day"})

    def test_saves_and_loads_raw_snapshot(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            with patch.object(usage_store, "DATA_DIR", Path(directory)):
                saved = usage_store.save_snapshot("detailed", self.payload)
                loaded = usage_store.load_snapshot("detailed")
                mode = S_IMODE((Path(directory) / "detailed.json").stat().st_mode)

        self.assertEqual(saved["source"], "detailed")
        self.assertEqual(loaded["payload"], self.payload)
        self.assertIn("capturedAt", loaded)
        self.assertEqual(mode, 0o600)


class ComparisonTest(unittest.TestCase):
    def test_compares_app_server_and_detailed_totals_by_date(self) -> None:
        app_server = {
            "summary": {"lifetimeTokens": 1000},
            "dailyUsageBuckets": [
                {"startDate": "2026-08-22", "tokens": 100},
                {"startDate": "2026-08-23", "tokens": 190},
                {"startDate": "2026-08-24", "tokens": 50},
            ],
        }
        detailed = {
            "groupBy": "day",
            "dailyUsageBuckets": [
                {"startDate": "2026-08-22", "textTotalTokens": 100},
                {"startDate": "2026-08-23", "textTotalTokens": 200},
            ],
            "totals": {},
        }

        comparison = usage_store.compare_sources(app_server, detailed)

        self.assertEqual(comparison["summary"]["matchedDays"], 2)
        self.assertEqual(comparison["summary"]["exactDays"], 1)
        self.assertEqual(comparison["summary"]["mismatchDays"], 1)
        self.assertEqual(comparison["summary"]["appServerOnlyDays"], 1)
        self.assertEqual(comparison["daily"][1]["differenceTokens"], 10)
        self.assertAlmostEqual(comparison["daily"][1]["differencePercent"], 5.2631579)


if __name__ == "__main__":
    unittest.main()
