export function filterBuckets(buckets, startDate, endDate) {
  if (!startDate || !endDate || startDate > endDate) {
    return [];
  }
  return buckets.filter(
    (bucket) => bucket.startDate >= startDate && bucket.startDate <= endDate,
  );
}

export function estimateCost(totalTokens, pricing) {
  const values = [
    totalTokens,
    pricing.inputRate,
    pricing.cachedRate,
    pricing.outputRate,
    pricing.cachedPercent,
    pricing.outputPercent,
  ];
  if (values.some((value) => !Number.isFinite(value) || value < 0)) {
    throw new Error("价格和比例必须是非负数");
  }
  const uncachedPercent = 100 - pricing.cachedPercent - pricing.outputPercent;
  if (uncachedPercent < 0) {
    throw new Error("缓存输入与输出占比之和不能超过 100%");
  }

  const tokenMillions = totalTokens / 1_000_000;
  const blendedRate =
    (uncachedPercent / 100) * pricing.inputRate +
    (pricing.cachedPercent / 100) * pricing.cachedRate +
    (pricing.outputPercent / 100) * pricing.outputRate;
  return {
    uncachedPercent,
    blendedRate,
    cost: Number((tokenMillions * blendedRate).toFixed(12)),
  };
}

export function estimateDetailedCost(buckets, pricing) {
  const rates = [pricing.inputRate, pricing.cachedRate, pricing.outputRate];
  if (rates.some((value) => !Number.isFinite(value) || value < 0)) {
    throw new Error("价格必须是非负数");
  }
  const totals = buckets.reduce(
    (result, bucket) => {
      const values = [
        bucket.cachedTextInputTokens,
        bucket.uncachedTextInputTokens,
        bucket.textOutputTokens,
      ];
      if (values.some((value) => !Number.isFinite(value) || value < 0)) {
        throw new Error("详细 Token 分类必须是非负数");
      }
      return {
        cached: result.cached + bucket.cachedTextInputTokens,
        uncached: result.uncached + bucket.uncachedTextInputTokens,
        output: result.output + bucket.textOutputTokens,
      };
    },
    { cached: 0, uncached: 0, output: 0 },
  );
  const totalTokens = totals.cached + totals.uncached + totals.output;
  const cost =
    (totals.cached / 1_000_000) * pricing.cachedRate +
    (totals.uncached / 1_000_000) * pricing.inputRate +
    (totals.output / 1_000_000) * pricing.outputRate;
  const percentage = (value) =>
    totalTokens === 0 ? 0 : Number(((value / totalTokens) * 100).toFixed(2));
  return {
    totalTokens,
    cachedTokens: totals.cached,
    uncachedTokens: totals.uncached,
    outputTokens: totals.output,
    cachedPercent: percentage(totals.cached),
    uncachedPercent: percentage(totals.uncached),
    outputPercent: percentage(totals.output),
    cost: Number(cost.toFixed(12)),
  };
}

export function mergeDailyUsage(appBuckets, detailedBuckets, pricing) {
  const ratesAreValid = [
    pricing.inputRate,
    pricing.cachedRate,
    pricing.outputRate,
  ].every((value) => Number.isFinite(value) && value >= 0);
  const appByDate = new Map(appBuckets.map((bucket) => [bucket.startDate, bucket]));
  const detailedByDate = new Map(
    detailedBuckets.map((bucket) => [bucket.startDate, bucket]),
  );
  return [...new Set([...appByDate.keys(), ...detailedByDate.keys()])]
    .sort()
    .map((startDate) => {
      const app = appByDate.get(startDate);
      const detailed = detailedByDate.get(startDate);
      const exactCost = detailed && ratesAreValid
        ? estimateDetailedCost([detailed], pricing).cost
        : null;
      return {
        startDate,
        appServerTokens: app?.tokens ?? null,
        cachedTextInputTokens: detailed?.cachedTextInputTokens ?? null,
        uncachedTextInputTokens: detailed?.uncachedTextInputTokens ?? null,
        textOutputTokens: detailed?.textOutputTokens ?? null,
        detailedTotalTokens: detailed?.textTotalTokens ?? null,
        differenceTokens:
          app && detailed ? detailed.textTotalTokens - app.tokens : null,
        exactCost,
      };
    });
}
