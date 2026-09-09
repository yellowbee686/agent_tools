export const MODEL_RATES = {
  "gpt-6-astra": {
    label: "GPT-6 Astra",
    inputRate: 10,
    cachedRate: 1,
    outputRate: 50,
  },
  "gpt-5.6-sol": {
    label: "GPT-5.6 Sol",
    inputRate: 4,
    cachedRate: 0.4,
    outputRate: 20,
  },
  "gpt-5.6-terra": {
    label: "GPT-5.6 Terra",
    inputRate: 2,
    cachedRate: 0.2,
    outputRate: 12,
  },
  "gpt-5.6-luna": {
    label: "GPT-5.6 Luna",
    inputRate: 0.2,
    cachedRate: 0.02,
    outputRate: 1.2,
  },
};

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

export function blendModelRates(models) {
  if (!Array.isArray(models) || models.length === 0) {
    return null;
  }
  const turnsByModel = new Map();
  for (const entry of models) {
    const turns = entry.turns;
    if (!Number.isFinite(turns) || turns < 0) {
      throw new Error("模型 turns 必须是非负数");
    }
    if (turns === 0) {
      continue;
    }
    if (!MODEL_RATES[entry.model]) {
      throw new Error(`没有官方 API 单价: ${entry.model}`);
    }
    turnsByModel.set(entry.model, (turnsByModel.get(entry.model) ?? 0) + turns);
  }
  const pricedTurns = [...turnsByModel.values()].reduce((sum, turns) => sum + turns, 0);
  if (pricedTurns === 0) {
    return null;
  }
  const shares = [...turnsByModel.entries()].map(([model, turns]) => {
    const rates = MODEL_RATES[model];
    return {
      model,
      label: rates.label,
      turns,
      percent: Number(((turns / pricedTurns) * 100).toFixed(2)),
      inputRate: rates.inputRate,
      cachedRate: rates.cachedRate,
      outputRate: rates.outputRate,
    };
  });
  const weighted = (field) =>
    shares.reduce((sum, share) => sum + (share.turns / pricedTurns) * share[field], 0);
  return {
    inputRate: weighted("inputRate"),
    cachedRate: weighted("cachedRate"),
    outputRate: weighted("outputRate"),
    pricedTurns,
    shares,
  };
}

export function summarizeModelMix(buckets) {
  return blendModelRates(buckets.flatMap((bucket) => bucket.models ?? []));
}

export function detailedBucketCost(bucket) {
  const rates = blendModelRates(bucket.models);
  if (!rates) {
    return null;
  }
  return estimateDetailedCost([bucket], rates).cost;
}

export function mergeDailyUsage(appBuckets, detailedBuckets) {
  const appByDate = new Map(appBuckets.map((bucket) => [bucket.startDate, bucket]));
  const detailedByDate = new Map(
    detailedBuckets.map((bucket) => [bucket.startDate, bucket]),
  );
  return [...new Set([...appByDate.keys(), ...detailedByDate.keys()])]
    .sort()
    .map((startDate) => {
      const app = appByDate.get(startDate);
      const detailed = detailedByDate.get(startDate);
      let exactCost = null;
      if (detailed) {
        try {
          exactCost = detailedBucketCost(detailed);
        } catch {
          exactCost = null;
        }
      }
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
