import {
  blendModelRates,
  detailedBucketCost,
  estimateCost,
  estimateDetailedCost,
  filterBuckets,
  mergeDailyUsage,
  summarizeModelMix,
} from "./calculations.js";

const elements = {
  refreshButton: document.querySelector("#refresh-button"),
  importButton: document.querySelector("#import-button"),
  importPanel: document.querySelector("#import-panel"),
  detailedJson: document.querySelector("#detailed-json"),
  saveImport: document.querySelector("#save-import"),
  importStatus: document.querySelector("#import-status"),
  errorBanner: document.querySelector("#error-banner"),
  startDate: document.querySelector("#start-date"),
  endDate: document.querySelector("#end-date"),
  rangeNote: document.querySelector("#range-note"),
  totalTokens: document.querySelector("#total-tokens"),
  tokenDetail: document.querySelector("#token-detail"),
  estimatedCost: document.querySelector("#estimated-cost"),
  costDetail: document.querySelector("#cost-detail"),
  lastUpdated: document.querySelector("#last-updated"),
  chart: document.querySelector("#usage-chart"),
  chartSkeleton: document.querySelector("#chart-skeleton"),
  chartEmpty: document.querySelector("#chart-empty"),
  usageTable: document.querySelector("#usage-table"),
  validationBadge: document.querySelector("#validation-badge"),
  appSourceState: document.querySelector("#app-source-state"),
  detailedSourceState: document.querySelector("#detailed-source-state"),
  validationSummary: document.querySelector("#validation-summary"),
  comparisonDetails: document.querySelector("#comparison-details"),
  comparisonTable: document.querySelector("#comparison-table"),
  costSource: document.querySelector("#cost-source"),
  costSourceHelp: document.querySelector("#cost-source-help"),
  modelMix: document.querySelector("#model-mix"),
  inputRate: document.querySelector("#input-rate"),
  cachedRate: document.querySelector("#cached-rate"),
  outputRate: document.querySelector("#output-rate"),
  cachedPercent: document.querySelector("#cached-percent"),
  outputPercent: document.querySelector("#output-percent"),
  uncachedPercent: document.querySelector("#uncached-percent"),
  shareSourceHelp: document.querySelector("#share-source-help"),
  pricingError: document.querySelector("#pricing-error"),
  blendedRate: document.querySelector("#blended-rate"),
  formulaCopy: document.querySelector("#formula-copy"),
};

const STATUS_LABELS = {
  exact: "一致",
  close: "接近",
  mismatch: "有差异",
  app_server_only: "仅 App-server",
  detailed_only: "仅详细响应",
};

const numberFormatter = new Intl.NumberFormat("zh-CN");
const compactFormatter = new Intl.NumberFormat("zh-CN", {
  notation: "compact",
  maximumFractionDigits: 2,
});
const usdFormatter = new Intl.NumberFormat("en-US", {
  style: "currency",
  currency: "USD",
  minimumFractionDigits: 2,
  maximumFractionDigits: 2,
});
const dateFormatter = new Intl.DateTimeFormat("zh-CN", {
  month: "short",
  day: "numeric",
  timeZone: "UTC",
});
const timestampFormatter = new Intl.DateTimeFormat("zh-CN", {
  month: "short",
  day: "numeric",
  hour: "2-digit",
  minute: "2-digit",
});

let usageData = null;
let estimatedShares = { cachedPercent: 90, outputPercent: 1 };

function formatRate(value) {
  return Number.isFinite(value) ? `$${Number(value.toFixed(4)).toString()}` : "—";
}

function parseNumber(input) {
  return Number.parseFloat(input.value);
}

function estimatedPricing(rates) {
  return {
    ...rates,
    ...estimatedShares,
  };
}

function formatTokens(value) {
  return `${compactFormatter.format(value)} tokens`;
}

function formatFullTokens(value) {
  return value == null ? "—" : numberFormatter.format(value);
}

function formatDate(value) {
  return dateFormatter.format(new Date(`${value}T00:00:00Z`));
}

function formatTimestamp(value) {
  return value ? timestampFormatter.format(new Date(value)) : "尚未捕获";
}

function setLoading(isLoading) {
  elements.refreshButton.disabled = isLoading;
  elements.refreshButton.textContent = isLoading ? "正在读取…" : "刷新账户数据";
  elements.chartSkeleton.hidden = !isLoading;
}

function showError(message) {
  elements.errorBanner.textContent = message;
  elements.errorBanner.hidden = !message;
}

function showImportStatus(message, kind = "") {
  elements.importStatus.textContent = message;
  elements.importStatus.className = kind;
}

function renderModelMix(mix) {
  if (!mix) {
    elements.modelMix.innerHTML = '<li class="empty-mix">尚未读到模型 turns</li>';
    elements.inputRate.textContent = "—";
    elements.cachedRate.textContent = "—";
    elements.outputRate.textContent = "—";
    return;
  }
  elements.modelMix.innerHTML = mix.shares
    .map(
      (share) =>
        `<li><span>${share.label}</span><strong>${share.percent}% · ${share.turns} turns</strong></li>`,
    )
    .join("");
  elements.inputRate.textContent = formatRate(mix.inputRate);
  elements.cachedRate.textContent = formatRate(mix.cachedRate);
  elements.outputRate.textContent = formatRate(mix.outputRate);
}

function setShareControls(values, isDetailed) {
  elements.cachedPercent.value = values.cachedPercent;
  elements.outputPercent.value = values.outputPercent;
  elements.cachedPercent.readOnly = isDetailed;
  elements.outputPercent.readOnly = isDetailed;
  elements.uncachedPercent.textContent = `${values.uncachedPercent}%`;
  elements.shareSourceHelp.textContent = isDetailed
    ? "比例来自已导入响应的准确 Token 分类。"
    : "当前比例是可调估算。";
}

function renderChart(rows) {
  elements.chart.replaceChildren();
  elements.chartEmpty.hidden = rows.length > 0;
  if (rows.length === 0) {
    return;
  }

  const namespace = "http://www.w3.org/2000/svg";
  const width = 960;
  const height = 320;
  const margin = { top: 16, right: 12, bottom: 34, left: 64 };
  const plotWidth = width - margin.left - margin.right;
  const plotHeight = height - margin.top - margin.bottom;
  const maxTokens = Math.max(
    ...rows.map((row) => row.detailedTotalTokens ?? row.appServerTokens ?? 0),
    1,
  );

  for (let index = 0; index <= 4; index += 1) {
    const ratio = index / 4;
    const y = margin.top + plotHeight * ratio;
    const line = document.createElementNS(namespace, "line");
    line.setAttribute("class", "chart-grid");
    line.setAttribute("x1", margin.left);
    line.setAttribute("x2", width - margin.right);
    line.setAttribute("y1", y);
    line.setAttribute("y2", y);
    elements.chart.append(line);

    const label = document.createElementNS(namespace, "text");
    label.setAttribute("class", "chart-axis-label");
    label.setAttribute("x", margin.left - 10);
    label.setAttribute("y", y + 4);
    label.setAttribute("text-anchor", "end");
    label.textContent = compactFormatter.format(maxTokens * (1 - ratio));
    elements.chart.append(label);
  }

  const slot = plotWidth / rows.length;
  const gap = Math.min(5, slot * 0.28);
  rows.forEach((row, index) => {
    const x = margin.left + index * slot + gap / 2;
    const barWidth = Math.max(1, slot - gap);
    if (row.detailedTotalTokens == null) {
      const barHeight = ((row.appServerTokens ?? 0) / maxTokens) * plotHeight;
      const bar = document.createElementNS(namespace, "rect");
      bar.setAttribute("class", "chart-bar fallback");
      bar.setAttribute("x", x);
      bar.setAttribute("y", margin.top + plotHeight - barHeight);
      bar.setAttribute("width", barWidth);
      bar.setAttribute("height", Math.max(1, barHeight));
      const title = document.createElementNS(namespace, "title");
      title.textContent = `${row.startDate}: App-server ${formatFullTokens(row.appServerTokens)} tokens`;
      bar.append(title);
      elements.chart.append(bar);
      return;
    }

    let y = margin.top + plotHeight;
    for (const [field, className, label] of [
      ["cachedTextInputTokens", "cached", "缓存输入"],
      ["uncachedTextInputTokens", "uncached", "未缓存输入"],
      ["textOutputTokens", "output", "输出"],
    ]) {
      const value = row[field];
      const segmentHeight = (value / maxTokens) * plotHeight;
      y -= segmentHeight;
      const segment = document.createElementNS(namespace, "rect");
      segment.setAttribute("class", `chart-bar ${className}`);
      segment.setAttribute("x", x);
      segment.setAttribute("y", y);
      segment.setAttribute("width", barWidth);
      segment.setAttribute("height", Math.max(1, segmentHeight));
      const title = document.createElementNS(namespace, "title");
      title.textContent = `${row.startDate} ${label}: ${formatFullTokens(value)} tokens`;
      segment.append(title);
      elements.chart.append(segment);
    }
  });

  const labelIndexes = [...new Set([0, Math.floor((rows.length - 1) / 2), rows.length - 1])];
  for (const index of labelIndexes) {
    const label = document.createElementNS(namespace, "text");
    label.setAttribute("class", "chart-axis-label");
    label.setAttribute("x", margin.left + index * slot + slot / 2);
    label.setAttribute("y", height - 7);
    label.setAttribute("text-anchor", "middle");
    label.textContent = formatDate(rows[index].startDate);
    elements.chart.append(label);
  }
}

function renderTable(rows) {
  const renderedRows = [...rows].reverse().map((row) => {
    const difference = row.differenceTokens == null
      ? "—"
      : `${row.differenceTokens > 0 ? "+" : ""}${formatFullTokens(row.differenceTokens)}`;
    return `
      <tr>
        <td>${row.startDate}</td>
        <td>${formatFullTokens(row.cachedTextInputTokens)}</td>
        <td>${formatFullTokens(row.uncachedTextInputTokens)}</td>
        <td>${formatFullTokens(row.textOutputTokens)}</td>
        <td>${formatFullTokens(row.detailedTotalTokens)}</td>
        <td>${formatFullTokens(row.appServerTokens)}</td>
        <td>${difference}</td>
        <td>${row.exactCost == null ? "—" : usdFormatter.format(row.exactCost)}</td>
      </tr>
    `;
  });
  elements.usageTable.innerHTML = renderedRows.join("");
}

function renderComparison(startDate, endDate) {
  const appSource = usageData.sources.appServer;
  const detailedSource = usageData.sources.detailed;
  elements.appSourceState.textContent = `已保存 · ${formatTimestamp(appSource.capturedAt)}`;
  elements.detailedSourceState.textContent = detailedSource
    ? `已保存 · ${formatTimestamp(detailedSource.capturedAt)}`
    : "尚未导入";

  const detailedOption = elements.costSource.querySelector('option[value="detailed"]');
  detailedOption.disabled = !detailedSource;
  if (!detailedSource || !usageData.comparison) {
    elements.validationBadge.className = "validation-badge waiting";
    elements.validationBadge.textContent = "等待详细数据";
    elements.validationSummary.innerHTML = "<p>导入详细响应后，会按日期对比两边的总 tokens。</p>";
    elements.comparisonDetails.hidden = true;
    return;
  }

  const rows = usageData.comparison.daily.filter(
    (row) => row.startDate >= startDate && row.startDate <= endDate,
  );
  const counts = {
    exact: 0,
    close: 0,
    mismatch: 0,
    app_server_only: 0,
    detailed_only: 0,
  };
  rows.forEach((row) => {
    counts[row.status] += 1;
  });
  const hasIssue = counts.mismatch + counts.app_server_only + counts.detailed_only > 0;
  elements.validationBadge.className = `validation-badge ${hasIssue ? "warning" : "valid"}`;
  elements.validationBadge.textContent = hasIssue ? "发现差异" : "校验通过";
  elements.validationSummary.innerHTML = `
    <p><strong>${counts.exact}</strong> 天完全一致</p>
    <p><strong>${counts.close}</strong> 天差异不超过 1%</p>
    <p><strong>${counts.mismatch}</strong> 天明显不一致</p>
    <p><strong>${counts.app_server_only + counts.detailed_only}</strong> 天仅单边存在</p>
  `;
  elements.comparisonTable.innerHTML = [...rows]
    .reverse()
    .map((row) => {
      const missing = row.status.endsWith("only");
      const statusClass = row.status === "exact" || row.status === "close"
        ? `status-${row.status}`
        : missing
          ? "status-missing"
          : "status-mismatch";
      const difference = row.differenceTokens == null
        ? "—"
        : `${row.differenceTokens > 0 ? "+" : ""}${formatFullTokens(row.differenceTokens)}`;
      return `
        <tr>
          <td>${row.startDate}</td>
          <td>${formatFullTokens(row.appServerTokens)}</td>
          <td>${formatFullTokens(row.detailedTokens)}</td>
          <td>${difference}</td>
          <td class="${statusClass}">${STATUS_LABELS[row.status]}</td>
        </tr>
      `;
    })
    .join("");
  elements.comparisonDetails.hidden = rows.length === 0;
}

function completeDetailedCoverage(detailedBuckets, startDate, endDate) {
  if (detailedBuckets.length === 0) {
    return false;
  }
  const detailedDates = new Set(detailedBuckets.map((bucket) => bucket.startDate));
  const cursor = new Date(`${startDate}T00:00:00Z`);
  const end = new Date(`${endDate}T00:00:00Z`);
  while (cursor <= end) {
    if (!detailedDates.has(cursor.toISOString().slice(0, 10))) {
      return false;
    }
    cursor.setUTCDate(cursor.getUTCDate() + 1);
  }
  return true;
}

function renderCost(appBuckets, startDate, endDate) {
  const detailedSource = usageData.sources.detailed;
  const detailedBuckets = detailedSource
    ? filterBuckets(detailedSource.dailyUsageBuckets, startDate, endDate)
    : [];
  const hasDetailedCoverage = completeDetailedCoverage(
    detailedBuckets,
    startDate,
    endDate,
  );
  const requestedSource = elements.costSource.value;
  const useDetailed = requestedSource === "detailed" || (
    requestedSource === "auto" && hasDetailedCoverage
  );

  try {
    elements.pricingError.hidden = true;
    renderModelMix(summarizeModelMix(detailedBuckets));
    if (!detailedSource) {
      throw new Error("导入含模型 turns 的详细响应后才能按实际模型计价");
    }
    if (useDetailed) {
      if (!hasDetailedCoverage) {
        throw new Error("详细响应没有完整覆盖所选日期范围");
      }
      if (detailedBuckets.some((bucket) => bucket.componentDelta !== 0)) {
        throw new Error("详细响应存在未归类 tokens，不能作为准确费用依据");
      }
      let cost = 0;
      let pricedTokens = 0;
      const missingMixDays = [];
      for (const bucket of detailedBuckets) {
        const dayCost = detailedBucketCost(bucket);
        const tokenTotal = (
          bucket.cachedTextInputTokens
          + bucket.uncachedTextInputTokens
          + bucket.textOutputTokens
        );
        if (dayCost == null && tokenTotal > 0) {
          missingMixDays.push(bucket.startDate);
          continue;
        }
        cost += dayCost ?? 0;
        pricedTokens += tokenTotal;
      }
      if (pricedTokens === 0) {
        throw new Error("缺少可定价的模型 turns 占比");
      }
      const result = estimateDetailedCost(
        detailedBuckets,
        { inputRate: 0, cachedRate: 0, outputRate: 0 },
      );
      const mix = summarizeModelMix(detailedBuckets);
      setShareControls(result, true);
      elements.costSourceHelp.textContent = missingMixDays.length
        ? `按天用模型 turns 加权官方单价。${missingMixDays.join("、")} 缺少模型占比，未计入费用。`
        : "详细响应完整覆盖当前范围，按天用模型 turns 加权官方单价。";
      elements.estimatedCost.textContent = usdFormatter.format(cost);
      elements.costDetail.textContent = `${result.cachedPercent}% 缓存输入 · ${result.uncachedPercent}% 未缓存输入 · ${result.outputPercent}% 输出`;
      const blendedRate = cost / (pricedTokens / 1_000_000);
      elements.blendedRate.textContent = `${usdFormatter.format(blendedRate)} / 1M`;
      elements.formulaCopy.textContent = mix
        ? `按日求和 · ${mix.shares.map((share) => `${share.label} ${share.percent}%`).join(" · ")}`
        : "按日求和";
    } else {
      setShareControls(
        {
          ...estimatedShares,
          uncachedPercent: 100 - estimatedShares.cachedPercent - estimatedShares.outputPercent,
        },
        false,
      );
      const detailedByDate = new Map(
        detailedBuckets.map((bucket) => [bucket.startDate, bucket]),
      );
      let cost = 0;
      let pricedTokens = 0;
      let lastResult = null;
      for (const bucket of appBuckets) {
        const rates = blendModelRates(detailedByDate.get(bucket.startDate)?.models);
        if (!rates) {
          continue;
        }
        lastResult = estimateCost(bucket.tokens, estimatedPricing(rates));
        cost += lastResult.cost;
        pricedTokens += bucket.tokens;
      }
      if (pricedTokens === 0) {
        throw new Error("导入含模型 turns 的详细响应后才能按实际模型计价");
      }
      const mix = summarizeModelMix(detailedBuckets);
      const blendedRate = cost / (pricedTokens / 1_000_000);
      elements.costSourceHelp.textContent = "详细响应未完整覆盖当前范围，已用有模型占比的日期做比例估算。";
      elements.uncachedPercent.textContent = lastResult
        ? `${lastResult.uncachedPercent}%`
        : `${100 - estimatedShares.cachedPercent - estimatedShares.outputPercent}%`;
      elements.blendedRate.textContent = `${usdFormatter.format(blendedRate)} / 1M`;
      elements.formulaCopy.textContent = mix
        ? `${100 - estimatedShares.cachedPercent - estimatedShares.outputPercent}% / ${estimatedShares.cachedPercent}% / ${estimatedShares.outputPercent}% × turns 加权官方单价`
        : "等待模型占比";
      elements.estimatedCost.textContent = usdFormatter.format(cost);
      elements.costDetail.textContent = `${estimatedShares.cachedPercent}% 缓存输入 · ${100 - estimatedShares.cachedPercent - estimatedShares.outputPercent}% 未缓存输入 · ${estimatedShares.outputPercent}% 输出`;
    }
    elements.estimatedCost.classList.remove("skeleton-text");
  } catch (error) {
    elements.pricingError.textContent = error.message;
    elements.pricingError.hidden = false;
    elements.uncachedPercent.textContent = "—";
    elements.blendedRate.textContent = "无法计算";
    elements.formulaCopy.textContent = "请修正价格、Token 占比或数据覆盖范围";
    elements.estimatedCost.textContent = "—";
    elements.costDetail.textContent = "费用数据无效";
  }
}

function render() {
  if (!usageData) {
    return;
  }
  const startDate = elements.startDate.value;
  const endDate = elements.endDate.value;
  if (!startDate || !endDate || startDate > endDate) {
    elements.rangeNote.textContent = "开始日期不能晚于结束日期";
    elements.rangeNote.classList.add("invalid");
    return;
  }
  elements.rangeNote.textContent = "包含开始和结束当天";
  elements.rangeNote.classList.remove("invalid");

  const appBuckets = filterBuckets(usageData.dailyUsageBuckets, startDate, endDate);
  const detailedBuckets = usageData.sources.detailed
    ? filterBuckets(usageData.sources.detailed.dailyUsageBuckets, startDate, endDate)
    : [];
  const detailedIsComplete = completeDetailedCoverage(
    detailedBuckets,
    startDate,
    endDate,
  ) && detailedBuckets.every((bucket) => bucket.componentDelta === 0);
  const appTotalTokens = appBuckets.reduce((total, bucket) => total + bucket.tokens, 0);
  const detailedTotalTokens = detailedBuckets.reduce(
    (total, bucket) => total + bucket.textTotalTokens,
    0,
  );
  const measuredTokens = detailedIsComplete ? detailedTotalTokens : appTotalTokens;
  const measuredDays = detailedIsComplete ? detailedBuckets.length : appBuckets.length;
  const averageTokens = measuredDays === 0 ? 0 : measuredTokens / measuredDays;
  const measuredSource = detailedIsComplete ? "详细响应准确分类" : "App-server 总量";
  elements.totalTokens.textContent = formatTokens(measuredTokens);
  elements.totalTokens.title = `${formatFullTokens(measuredTokens)} tokens`;
  elements.tokenDetail.textContent = `${measuredDays} 天 · 日均 ${compactFormatter.format(averageTokens)} tokens · ${measuredSource}`;
  elements.totalTokens.classList.remove("skeleton-text");

  renderComparison(startDate, endDate);
  renderCost(appBuckets, startDate, endDate);
  const dailyRows = mergeDailyUsage(appBuckets, detailedBuckets);
  renderChart(dailyRows);
  renderTable(dailyRows);
}

function applyPayload(payload, preserveDates = false) {
  usageData = payload;
  const detailedDates = (payload.sources.detailed?.dailyUsageBuckets || [])
    .map((bucket) => bucket.startDate)
    .sort();
  if (!preserveDates) {
    elements.startDate.value = detailedDates[0] ?? payload.defaultWindow.startDate;
    elements.endDate.value = detailedDates.at(-1) ?? payload.defaultWindow.endDateInclusive;
  }
  const allDates = [
    ...payload.dailyUsageBuckets.map((bucket) => bucket.startDate),
    ...detailedDates,
  ].sort();
  if (allDates.length > 0) {
    for (const input of [elements.startDate, elements.endDate]) {
      input.min = allDates[0];
      input.max = allDates.at(-1);
    }
  }
  elements.lastUpdated.textContent = `更新于 ${new Date(payload.generatedAt).toLocaleTimeString("zh-CN", {
    hour: "2-digit",
    minute: "2-digit",
  })}`;
  render();
}

async function loadUsage() {
  setLoading(true);
  showError("");
  try {
    const response = await fetch("/api/usage", { cache: "no-store" });
    const payload = await response.json();
    if (!response.ok) {
      throw new Error(payload.error || "无法读取账户用量");
    }
    applyPayload(payload);
  } catch (error) {
    showError(`${error.message}。请确认 Codex CLI 已登录当前 ChatGPT 账户。`);
    elements.totalTokens.textContent = "读取失败";
    elements.tokenDetail.textContent = "未获得账户数据";
    elements.estimatedCost.textContent = "—";
    elements.costDetail.textContent = "等待用量数据";
  } finally {
    setLoading(false);
  }
}

async function saveDetailedImport() {
  showImportStatus("正在校验并保存…");
  elements.saveImport.disabled = true;
  try {
    const payload = JSON.parse(elements.detailedJson.value);
    const response = await fetch("/api/detailed-usage", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
    const result = await response.json();
    if (!response.ok) {
      throw new Error(result.error || "无法导入详细响应");
    }
    applyPayload(result);
    elements.detailedJson.value = "";
    showImportStatus("已保存，双源校验已更新", "success");
  } catch (error) {
    const message = error instanceof SyntaxError ? "粘贴内容不是有效 JSON" : error.message;
    showImportStatus(message, "error");
  } finally {
    elements.saveImport.disabled = false;
  }
}

elements.refreshButton.addEventListener("click", loadUsage);
elements.importButton.addEventListener("click", () => {
  const willOpen = elements.importPanel.hidden;
  elements.importPanel.hidden = !willOpen;
  elements.importButton.setAttribute("aria-expanded", String(willOpen));
  if (willOpen) {
    elements.detailedJson.focus();
  }
});
elements.saveImport.addEventListener("click", saveDetailedImport);
elements.startDate.addEventListener("change", render);
elements.endDate.addEventListener("change", render);
elements.costSource.addEventListener("change", render);
elements.cachedPercent.addEventListener("input", () => {
  if (!elements.cachedPercent.readOnly) {
    estimatedShares.cachedPercent = parseNumber(elements.cachedPercent);
    render();
  }
});
elements.outputPercent.addEventListener("input", () => {
  if (!elements.outputPercent.readOnly) {
    estimatedShares.outputPercent = parseNumber(elements.outputPercent);
    render();
  }
});

loadUsage();
