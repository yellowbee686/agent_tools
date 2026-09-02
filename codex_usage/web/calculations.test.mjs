import test from "node:test";
import assert from "node:assert/strict";

import {
  estimateCost,
  estimateDetailedCost,
  filterBuckets,
  mergeDailyUsage,
} from "./calculations.js";


test("filters an inclusive browser date range", () => {
  const buckets = [
    { startDate: "2026-07-24", tokens: 5 },
    { startDate: "2026-07-25", tokens: 100 },
    { startDate: "2026-08-23", tokens: 300 },
    { startDate: "2026-08-24", tokens: 400 },
  ];

  assert.deepEqual(filterBuckets(buckets, "2026-07-25", "2026-08-23"), [
    { startDate: "2026-07-25", tokens: 100 },
    { startDate: "2026-08-23", tokens: 300 },
  ]);
});

test("estimates API-equivalent cost from explicit token shares", () => {
  const result = estimateCost(1_000_000, {
    inputRate: 4,
    cachedRate: 0.4,
    outputRate: 20,
    cachedPercent: 90,
    outputPercent: 1,
  });

  assert.equal(result.uncachedPercent, 9);
  assert.equal(result.cost, 0.92);
});

test("rejects token shares above one hundred percent", () => {
  assert.throws(
    () =>
      estimateCost(100, {
        inputRate: 4,
        cachedRate: 0.4,
        outputRate: 20,
        cachedPercent: 95,
        outputPercent: 10,
      }),
    /100%/,
  );
});

test("calculates exact category cost from imported detailed tokens", () => {
  const result = estimateDetailedCost(
    [
      {
        cachedTextInputTokens: 600_000,
        uncachedTextInputTokens: 300_000,
        textOutputTokens: 100_000,
      },
    ],
    { inputRate: 4, cachedRate: 0.4, outputRate: 20 },
  );

  assert.equal(result.totalTokens, 1_000_000);
  assert.equal(result.cost, 3.44);
  assert.equal(result.cachedPercent, 60);
  assert.equal(result.uncachedPercent, 30);
  assert.equal(result.outputPercent, 10);
});

test("merges detailed categories with app-server totals and exact daily cost", () => {
  const rows = mergeDailyUsage(
    [
      { startDate: "2026-08-22", tokens: 110 },
      { startDate: "2026-08-23", tokens: 200 },
    ],
    [
      {
        startDate: "2026-08-22",
        cachedTextInputTokens: 60,
        uncachedTextInputTokens: 30,
        textOutputTokens: 10,
        textTotalTokens: 100,
      },
      {
        startDate: "2026-08-24",
        cachedTextInputTokens: 120,
        uncachedTextInputTokens: 50,
        textOutputTokens: 30,
        textTotalTokens: 200,
      },
    ],
    { inputRate: 4, cachedRate: 0.4, outputRate: 20 },
  );

  assert.deepEqual(rows.map((row) => row.startDate), [
    "2026-08-22",
    "2026-08-23",
    "2026-08-24",
  ]);
  assert.equal(rows[0].differenceTokens, -10);
  assert.equal(rows[0].exactCost, 0.000344);
  assert.equal(rows[1].cachedTextInputTokens, null);
  assert.equal(rows[1].exactCost, null);
  assert.equal(rows[2].appServerTokens, null);
});
