import { getDealerMap, getPriceHistory, getAggregateGex } from "./utils/api.js";
import { formatPrice, formatLargeNumber, formatPct, formatOI } from "./utils/formatters.js";

const $ = (sel) => document.querySelector(sel);
const $$ = (sel) => document.querySelectorAll(sel);

let currentData = null;
let refreshTimer = null;
let gexMode = "single";
let aggregateCache = null;

function init() {
  const tickerInput = $("#ticker");
  const analyzeBtn = $("#analyze-btn");
  const expirySelect = $("#expiry-select");
  const refreshSelect = $("#refresh-interval");
  const portfolioInput = $("#portfolio");

  const savedBalance = localStorage.getItem("portfolio_balance");
  if (savedBalance) portfolioInput.value = savedBalance;

  portfolioInput.addEventListener("input", () => {
    const val = portfolioInput.value;
    if (val) localStorage.setItem("portfolio_balance", val);
    else localStorage.removeItem("portfolio_balance");
  });

  analyzeBtn.addEventListener("click", () => runAnalysis());
  tickerInput.addEventListener("keydown", (e) => {
    if (e.key === "Enter") runAnalysis();
  });

  expirySelect.addEventListener("change", () => {
    const ticker = tickerInput.value.trim().toUpperCase();
    if (ticker) runAnalysis(expirySelect.value);
  });

  refreshSelect.addEventListener("change", () => {
    setupAutoRefresh();
  });

  $("#gex-single-btn").addEventListener("click", () => switchGexMode("single"));
  $("#gex-agg-btn").addEventListener("click", () => switchGexMode("aggregate"));

  $$(".empty-state__chip").forEach((chip) => {
    chip.addEventListener("click", () => {
      tickerInput.value = chip.dataset.ticker;
      runAnalysis();
    });
  });

  $("#error-close").addEventListener("click", () => {
    $("#error").style.display = "none";
  });
}

function setupAutoRefresh() {
  if (refreshTimer) {
    clearInterval(refreshTimer);
    refreshTimer = null;
  }
  const seconds = parseInt($("#refresh-interval").value, 10);
  if (seconds <= 0 || !currentData) return;

  refreshTimer = setInterval(() => {
    const ticker = $("#ticker").value.trim().toUpperCase();
    if (!ticker) return;
    const expiry = $("#expiry-select").value || null;
    runAnalysis(expiry, true);
  }, seconds * 1000);
}

function updateTimestamp() {
  const el = $("#last-updated");
  const timeEl = $("#last-updated-time");
  el.style.display = "flex";
  const now = new Date();
  timeEl.textContent = now.toLocaleTimeString();
  el.classList.add("last-updated--pulse");
  setTimeout(() => el.classList.remove("last-updated--pulse"), 1200);
}

async function runAnalysis(expiration = null, isAutoRefresh = false) {
  const ticker = $("#ticker").value.trim().toUpperCase();
  if (!ticker) return;

  $("#ticker").value = ticker;
  if (!isAutoRefresh) showLoading(true);
  hideError();

  try {
    const portfolioVal = $("#portfolio").value;
    const accountSize = portfolioVal ? parseFloat(portfolioVal) : null;

    const [data, priceHistory] = await Promise.all([
      getDealerMap(ticker, expiration, accountSize),
      getPriceHistory(ticker),
    ]);
    currentData = data;
    currentData._priceHistory = priceHistory;
    aggregateCache = null;
    gexMode = "single";
    renderDashboard(data);
    updateTimestamp();
    if (!isAutoRefresh) setupAutoRefresh();
  } catch (err) {
    showError(err.message);
    if (!isAutoRefresh) $("#dashboard").style.display = "none";
  } finally {
    if (!isAutoRefresh) showLoading(false);
  }
}

function showLoading(show) {
  $("#loading").style.display = show ? "flex" : "none";
  $("#empty-state").style.display = "none";
  if (show) $("#dashboard").style.display = "none";
  $("#analyze-btn").disabled = show;
}

function showError(msg) {
  $("#error-text").textContent = msg;
  $("#error").style.display = "flex";
}

function hideError() {
  $("#error").style.display = "none";
}

function renderDashboard(data) {
  $("#dashboard").style.display = "flex";
  renderExpirySelector(data);

  // Section 1: The Play
  renderDirectionalHero(data);
  renderPositions(data);

  // Vol Edge
  renderVolAnalysis(data);

  // Section 2: The Map
  renderCollisionTimeline(data);
  renderLevelActions(data);
  renderKeyLevels(data);
  renderAvoid(data);

  // Section 3: Vol Plays
  renderStraddles(data);

  // Section 4: Charts
  renderPriceChart(data);
  resetGexToggle();
  renderGexChart(data);
  renderOIChart(data);

  // Section 5: Regime & Technicals
  renderRegimeStrip(data);
  renderTechnicals(data);
}

/* ---- Expiry Selector ---- */
function renderExpirySelector(data) {
  const wrapper = $("#expiry-select-wrapper");
  const select = $("#expiry-select");
  wrapper.style.display = "block";

  const all = data.available_expirations;
  const selected = data.expiration;

  // Pick ~4 meaningful expirations: nearest weekly, ~7 DTE, ~14 DTE, ~30 DTE
  const targets = [3, 7, 14, 30];
  const picked = new Set();
  for (const t of targets) {
    const best = all.reduce((a, b) => Math.abs(a.dte - t) < Math.abs(b.dte - t) ? a : b, all[0]);
    if (best) picked.add(best.date);
  }
  // Always include the currently selected one
  picked.add(selected);

  // Build featured + rest
  const featured = all.filter((e) => picked.has(e.date));
  const rest = all.filter((e) => !picked.has(e.date));

  let html = featured
    .map((e) => `<option value="${e.date}" ${e.date === selected ? "selected" : ""}>${e.date} (${e.dte} DTE)</option>`)
    .join("");

  if (rest.length) {
    html += `<option disabled>──────────</option>`;
    html += rest
      .map((e) => `<option value="${e.date}" ${e.date === selected ? "selected" : ""}>${e.date} (${e.dte} DTE)</option>`)
      .join("");
  }

  select.innerHTML = html;
}

/* ---- Price Chart ---- */
function renderPriceChart(data) {
  const bars = data._priceHistory;
  const container = $("#price-chart");
  if (!bars || bars.length < 5) {
    container.innerHTML = `<p style="color: var(--text-muted); padding: 20px;">Price data unavailable.</p>`;
    return;
  }

  const recent = bars.slice(-40);
  const spot = data.spot;
  const keyLevels = data.key_levels;
  const ch = data.channel;

  const margin = { top: 30, right: 90, bottom: 40, left: 70 };
  const width = 900;
  const height = 360;
  const innerW = width - margin.left - margin.right;
  const innerH = height - margin.top - margin.bottom;

  const allPrices = recent.flatMap((b) => [b.high, b.low]);
  const vwap20 = data.technicals?.vwap?.vwap_20d?.value;
  const levelValues = [
    keyLevels.max_pain,
    keyLevels.flip_point,
    keyLevels.call_wall?.strike,
    keyLevels.put_wall?.strike,
    ch.floor,
    ch.ceiling,
    vwap20,
  ].filter((v) => v && v > 0);
  const allVals = [...allPrices, ...levelValues];
  const minP = Math.min(...allVals) * 0.998;
  const maxP = Math.max(...allVals) * 1.002;
  const priceRange = maxP - minP || 1;

  const barW = Math.max(Math.floor(innerW / recent.length) - 2, 3);
  const wickW = 1;

  const xScale = (i) => margin.left + (innerW / recent.length) * i + (innerW / recent.length - barW) / 2;
  const yScale = (val) => margin.top + innerH - ((val - minP) / priceRange) * innerH;

  let svg = `<svg viewBox="0 0 ${width} ${height}" width="100%" preserveAspectRatio="xMidYMid meet">`;

  // Channel shading
  if (ch.floor && ch.ceiling && ch.floor < maxP && ch.ceiling > minP) {
    const y1 = yScale(Math.min(ch.ceiling, maxP));
    const y2 = yScale(Math.max(ch.floor, minP));
    svg += `<rect x="${margin.left}" y="${y1}" width="${innerW}" height="${Math.max(y2 - y1, 0)}" fill="rgba(99,102,241,0.08)" rx="2"/>`;
  }

  // Horizontal grid lines
  const nTicks = 6;
  for (let i = 0; i <= nTicks; i++) {
    const val = minP + (priceRange / nTicks) * i;
    const y = yScale(val);
    svg += `<line x1="${margin.left}" y1="${y}" x2="${width - margin.right}" y2="${y}" stroke="var(--border-subtle)" stroke-width="0.5"/>`;
    svg += `<text x="${margin.left - 8}" y="${y + 3}" text-anchor="end" class="chart-label">$${formatPrice(val)}</text>`;
  }

  // Candlesticks
  recent.forEach((b, i) => {
    const x = xScale(i);
    const isUp = b.close >= b.open;
    const bodyTop = yScale(Math.max(b.open, b.close));
    const bodyBot = yScale(Math.min(b.open, b.close));
    const bodyH = Math.max(bodyBot - bodyTop, 1);
    const color = isUp ? "var(--green)" : "var(--red)";
    const wickX = x + barW / 2;

    svg += `<line x1="${wickX}" y1="${yScale(b.high)}" x2="${wickX}" y2="${yScale(b.low)}" stroke="${color}" stroke-width="${wickW}"/>`;
    svg += `<rect x="${x}" y="${bodyTop}" width="${barW}" height="${bodyH}" fill="${color}" opacity="${isUp ? 0.85 : 0.85}" rx="0.5">`;
    svg += `<title>${b.date}\nO: $${b.open} H: $${b.high} L: $${b.low} C: $${b.close}\nVol: ${formatLargeNumber(b.volume)}</title></rect>`;
  });

  // Date labels
  const dateInterval = Math.max(Math.floor(recent.length / 8), 1);
  recent.forEach((b, i) => {
    if (i % dateInterval === 0) {
      const x = xScale(i) + barW / 2;
      const label = b.date.slice(5);
      svg += `<text x="${x}" y="${height - 8}" text-anchor="middle" class="chart-label">${label}</text>`;
    }
  });

  // Level lines
  const vwapVal = data.technicals?.vwap?.vwap_20d?.value;
  const levels = [
    { value: keyLevels.flip_point, label: "FLIP", color: "#f59e0b" },
    { value: keyLevels.max_pain, label: "Max Pain", color: "#6366f1" },
    { value: keyLevels.call_wall?.strike, label: "Call Wall", color: "#ef4444" },
    { value: keyLevels.put_wall?.strike, label: "Put Wall", color: "#22c55e" },
    { value: ch.floor, label: "Ch Floor", color: "#22c55e", dashed: true },
    { value: ch.ceiling, label: "Ch Ceiling", color: "#ef4444", dashed: true },
    { value: vwapVal, label: "VWAP", color: "#06b6d4", dashed: true },
  ];

  levels.forEach((lv) => {
    if (!lv.value || lv.value < minP || lv.value > maxP) return;
    const y = yScale(lv.value);
    const dash = lv.dashed ? 'stroke-dasharray="6 3"' : 'stroke-dasharray="4 3"';
    svg += `<line x1="${margin.left}" y1="${y}" x2="${width - margin.right}" y2="${y}" stroke="${lv.color}" stroke-width="1.5" ${dash} opacity="0.7"/>`;
    svg += `<text x="${width - margin.right + 4}" y="${y + 3}" class="chart-level-label-price" fill="${lv.color}">${lv.label} $${formatPrice(lv.value)}</text>`;
  });

  // Moving Average lines
  const allCloses = bars.map((b) => b.close);
  const startIdx = bars.length - recent.length;
  const maLines = [
    { period: 20, color: "#3b82f6", label: "20" },
    { period: 50, color: "#f59e0b", label: "50" },
  ];
  maLines.forEach((ma) => {
    if (allCloses.length < ma.period) return;
    const points = [];
    for (let i = 0; i < recent.length; i++) {
      const idx = startIdx + i;
      if (idx < ma.period - 1) continue;
      const slice = allCloses.slice(idx - ma.period + 1, idx + 1);
      const avg = slice.reduce((s, v) => s + v, 0) / slice.length;
      if (avg >= minP && avg <= maxP) {
        points.push(`${xScale(i) + barW / 2},${yScale(avg)}`);
      }
    }
    if (points.length > 1) {
      svg += `<polyline points="${points.join(" ")}" fill="none" stroke="${ma.color}" stroke-width="1.2" opacity="0.6"/>`;
      const lastPt = points[points.length - 1].split(",");
      svg += `<text x="${parseFloat(lastPt[0]) + 6}" y="${parseFloat(lastPt[1]) + 3}" class="chart-label" fill="${ma.color}" font-size="8">${ma.label}</text>`;
    }
  });

  // Spot marker on the last candle
  const lastX = xScale(recent.length - 1) + barW + 4;
  const spotY = yScale(spot);
  svg += `<line x1="${margin.left}" y1="${spotY}" x2="${width - margin.right}" y2="${spotY}" stroke="var(--accent)" stroke-width="1.5" stroke-dasharray="2 2" opacity="0.5"/>`;
  svg += `<circle cx="${xScale(recent.length - 1) + barW / 2}" cy="${spotY}" r="4" fill="var(--accent)"/>`;

  svg += "</svg>";
  container.innerHTML = svg;
}

/* ---- GEX Toggle ---- */
function resetGexToggle() {
  gexMode = "single";
  $("#gex-single-btn").classList.add("gex-toggle__btn--active");
  $("#gex-agg-btn").classList.remove("gex-toggle__btn--active");
  $("#gex-agg-info").style.display = "none";
}

async function switchGexMode(mode) {
  if (mode === gexMode || !currentData) return;
  gexMode = mode;

  const singleBtn = $("#gex-single-btn");
  const aggBtn = $("#gex-agg-btn");
  const aggInfo = $("#gex-agg-info");

  singleBtn.classList.toggle("gex-toggle__btn--active", mode === "single");
  aggBtn.classList.toggle("gex-toggle__btn--active", mode === "aggregate");

  if (mode === "single") {
    aggInfo.style.display = "none";
    renderGexChart(currentData);
    return;
  }

  // Aggregate mode
  if (!aggregateCache) {
    aggInfo.style.display = "block";
    aggInfo.innerHTML = `<span class="gex-agg-info__loading">Loading aggregate GEX across all expirations...</span>`;
    try {
      aggregateCache = await getAggregateGex(currentData.ticker);
    } catch (err) {
      aggInfo.innerHTML = `<span class="gex-agg-info__error">Failed to load aggregate GEX: ${err.message}</span>`;
      return;
    }
  }

  renderGexChartFromData(aggregateCache.by_strike, currentData.spot, {
    flip_point: aggregateCache.flip_point,
    abs_gamma_strike: aggregateCache.abs_gamma_strike,
    max_pain: aggregateCache.max_pain,
    call_wall: aggregateCache.call_wall,
    put_wall: aggregateCache.put_wall,
  }, currentData.channel);

  aggInfo.style.display = "block";
  const expList = aggregateCache.expirations_used.map((e) => `${e.expiration} (${e.dte}d)`).join(", ");
  aggInfo.innerHTML = `<span class="gex-agg-info__text">Aggregated across ${aggregateCache.n_expirations} expirations: ${expList}</span>`;
}

/* ---- Directional Hero ---- */
function renderDirectionalHero(data) {
  const d = data.directional;
  const bias = d.bias;
  const dirClass = bias.direction === "BULLISH" ? "bullish" : bias.direction === "BEARISH" ? "bearish" : "neutral";
  const strengthDots = bias.strength === "STRONG" ? 3 : bias.strength === "MODERATE" ? 2 : bias.strength === "WEAK" ? 1 : 0;

  const wb = d.wall_break;
  const tc = d.tech_context || {};
  const re = data.reynolds;
  const acf = data.acf_data;
  const ch = data.channel;

  // Inline regime tags for quick scan
  const gexTag = data.gex_regime === "POSITIVE_GAMMA" ? "Positive \u0393" : "Negative \u0393";
  const gexCls = data.gex_regime === "POSITIVE_GAMMA" ? "positive" : "negative";
  const reCls = re.regime === "LAMINAR" ? "laminar" : re.regime === "TURBULENT" ? "turbulent" : "transitional";
  const trendCls = tc.confirms_thesis ? "confirm" : tc.conflicts_thesis ? "conflict" : "neutral";

  const channelStr = ch.floor && ch.ceiling ? `$${formatPrice(ch.floor)}–$${formatPrice(ch.ceiling)}` : "—";
  const vwapCtx = tc.vwap || "N/A";
  const vwapLevel = tc.vwap_level ? `$${formatPrice(tc.vwap_level)}` : "";
  const vwapCls = vwapCtx === "ABOVE" || vwapCtx === "EXTENDED_ABOVE" ? "positive" : vwapCtx === "BELOW" || vwapCtx === "EXTENDED_BELOW" ? "negative" : "neutral";

  // Beta / entropy / SEI from new physics modules
  const betaVal = tc.beta || 1.0;
  const betaAdj = tc.beta_adj_factor || 1.0;
  const reAdj = tc.re_beta_adj || re.number;
  const entropyRegime = tc.entropy_regime || "DISPERSED";
  const seiVal = tc.sei || 0;
  const seiRegime = tc.sei_regime || "NONE";

  const betaTag = betaVal < 0.7 ? `<span class="hero-tag hero-tag--warning">&beta; ${betaVal.toFixed(2)} (${betaAdj.toFixed(1)}x amp)</span>` :
                  betaVal > 1.5 ? `<span class="hero-tag hero-tag--muted">&beta; ${betaVal.toFixed(2)} (dampened)</span>` : "";

  // Vol edge from vol_analysis
  const volEdge = data.vol_analysis?.vol_edge || {};
  const volIvHv = data.vol_analysis?.iv_hv || {};
  const volCls = volEdge.score >= 60 ? "positive" : volEdge.score >= 40 ? "neutral" : volEdge.score >= 25 ? "warning" : "negative";
  const volLabel = volIvHv.iv_hv_ratio ? `IV/HV ${volIvHv.iv_hv_ratio}` : "";
  const volVerdict = volEdge.verdict ? volEdge.verdict.replace(/_/g, " ") : "";
  const volTag = volLabel ? `<span class="hero-tag hero-tag--${volCls}">${volLabel} ${volVerdict}</span>` : "";

  const entropyCls = entropyRegime === "CRITICAL" ? "negative" : entropyRegime === "APPROACHING" ? "warning" : "neutral";
  const entropyTag = entropyRegime !== "DISPERSED" ? `<span class="hero-tag hero-tag--${entropyCls}">Entropy: ${entropyRegime}</span>` : "";

  const seiCls = seiRegime === "HIGH_EXCITATION" ? "negative" : seiRegime === "MODERATE_EXCITATION" ? "warning" : "";
  const seiTag = seiRegime !== "NONE" && seiRegime !== "LOW_EXCITATION"
    ? `<span class="hero-tag hero-tag--${seiCls}">SEI ${seiVal.toFixed(1)} ${seiRegime.replace(/_/g, " ")}</span>` : "";

  // Beta-adj Re display
  const reDisplay = betaAdj > 1.05 ? `Re ${re.number.toFixed(1)} (&beta;-adj ${reAdj.toFixed(1)})` : `Re ${re.number.toFixed(1)}`;

  $("#hero").innerHTML = `
    <div class="hero hero--${dirClass}">
      <div class="hero__top">
        <div class="hero__ticker">${data.ticker} <span class="hero__price">$${formatPrice(data.spot)}</span></div>
        <div class="hero__meta">${data.dte} DTE &middot; ${data.expiration} &middot; IV: ${d.atm_iv}%</div>
      </div>

      <div class="hero__core">
        <div class="hero__action">${bias.action}</div>
        <div class="hero__thesis">${d.thesis_label}</div>
        <div class="hero__desc">${bias.description}</div>
      </div>

      <div class="hero__tags">
        <span class="hero-tag hero-tag--${gexCls}">${gexTag}</span>
        <span class="hero-tag hero-tag--${reCls}">${reDisplay} ${re.regime}</span>
        <span class="hero-tag">ACF ${acf.mean_acf1 >= 0 ? "+" : ""}${acf.mean_acf1.toFixed(3)}</span>
        <span class="hero-tag">Ch ${channelStr}</span>
        <span class="hero-tag hero-tag--${trendCls}">${tc.ma_alignment?.replace(/_/g, " ") || "—"} &middot; ${tc.rs_label?.replace(/_/g, " ") || "—"}</span>
        ${vwapLevel ? `<span class="hero-tag hero-tag--${vwapCls}">VWAP ${vwapLevel} ${vwapCtx.replace(/_/g, " ")}</span>` : ""}
        ${volTag}
        ${betaTag}
        ${entropyTag}
        ${seiTag}
      </div>

      <div class="hero__bottom">
        <div class="hero__strength">
          ${Array.from({ length: 3 }, (_, i) => `<span class="hero__dot ${i < strengthDots ? 'hero__dot--active' : ''}"></span>`).join("")}
          <span class="hero__strength-label">${bias.strength}</span>
        </div>
        <div class="hero__wb">
          <span class="hero__wb-label">Wall Break ${wb.probability}%</span>
          <div class="hero__wb-bar"><div class="hero__wb-fill" style="width: ${wb.probability}%"></div></div>
          <span class="hero__wb-signal hero__wb-signal--${wb.re_says === 'BREAK' ? 'break' : 'hold'}">Re: ${wb.re_says}</span>
          <span class="hero__wb-signal hero__wb-signal--${wb.acf_says === 'BREAK' ? 'break' : 'hold'}">ACF: ${wb.acf_says}</span>
          ${wb.sei_says ? `<span class="hero__wb-signal hero__wb-signal--${wb.sei_says === 'BREAK' ? 'break' : 'hold'}">SEI: ${wb.sei_says}</span>` : ""}
        </div>
      </div>
    </div>
  `;
}

function _renderVolRegimeChips(data) {
  const va = data.vol_analysis;
  if (!va) return "";

  const ivhv = va.iv_hv || {};
  const skew = va.skew || {};
  const term = va.term_structure || {};
  const vrp = va.vrp || {};

  const ivCls = ivhv.context === "CHEAP" || ivhv.context === "SLIGHT_DISCOUNT" ? "stable"
    : ivhv.context === "EXPENSIVE" || ivhv.context === "VERY_EXPENSIVE" ? "critical" : "";
  const ivChip = ivhv.iv_hv_ratio ? `
    <div class="regime-chip${ivCls ? ` regime-chip--${ivCls}` : ""}">
      <span class="regime-chip__label">IV/HV Ratio</span>
      <span class="regime-chip__value">${ivhv.iv_hv_ratio} (${ivhv.context?.replace(/_/g, " ") || "—"})</span>
      <span class="regime-chip__sub">IV: ${ivhv.atm_iv}% | HV: ${ivhv.hv_used}% | Pctl: ${ivhv.iv_percentile_proxy}th</span>
    </div>` : "";

  const termCls = term.shape === "BACKWARDATION" ? "critical" : term.shape === "CONTANGO" ? "stable" : "";
  const termChip = term.shape && term.shape !== "UNKNOWN" ? `
    <div class="regime-chip${termCls ? ` regime-chip--${termCls}` : ""}">
      <span class="regime-chip__label">Term Structure</span>
      <span class="regime-chip__value">${term.shape.replace(/_/g, " ")}</span>
      <span class="regime-chip__sub">${term.front_iv}% → ${term.back_iv}% | Slope: ${term.slope > 0 ? "+" : ""}${term.slope}pts</span>
    </div>` : "";

  const vrpCls = vrp.context === "DISCOUNT" ? "stable" : vrp.context === "HIGH_PREMIUM" ? "critical" : vrp.context === "MODERATE_PREMIUM" ? "warning" : "";
  const vrpChip = vrp.context && vrp.context !== "N/A" ? `
    <div class="regime-chip${vrpCls ? ` regime-chip--${vrpCls}` : ""}">
      <span class="regime-chip__label">VRP (GEX-adj)</span>
      <span class="regime-chip__value">${vrp.vrp_gex_adjusted > 0 ? "+" : ""}${vrp.vrp_gex_adjusted} (${vrp.context?.replace(/_/g, " ") || "—"})</span>
      <span class="regime-chip__sub">GEX-implied HV: ${vrp.gex_implied_hv}% (${vrp.gex_vol_mult}x) | Drag: ${vrp.daily_vrp_drag}/day</span>
    </div>` : "";

  return ivChip + termChip + vrpChip;
}

/* ---- Regime Strip ---- */
function renderRegimeStrip(data) {
  const acf = data.acf_data;
  const re = data.reynolds;
  const ph = data.phase;
  const gex = data.gex_regime;
  const ch = data.channel;

  const gexCls = gex === "POSITIVE_GAMMA" ? "positive" : "negative";
  const acfCls = acf.mean_acf1 < -0.05 ? "dampened" : acf.mean_acf1 > 0.05 ? "amplified" : "neutral";
  const reCls = re.regime === "LAMINAR" ? "laminar" : re.regime === "TURBULENT" ? "turbulent" : "transitional";

  let channelHtml = "";
  if (ch.floor && ch.ceiling) {
    const degNote = ch.degenerate ? ' <span class="regime-chip__flag">(widened)</span>' : "";
    channelHtml = `
      <div class="regime-chip">
        <span class="regime-chip__label">Channel${degNote}</span>
        <span class="regime-chip__value">$${formatPrice(ch.floor)} — $${formatPrice(ch.ceiling)}</span>
        <span class="regime-chip__sub">Width: ${ch.width_pct}% | Pos: ${ch.channel_position !== null ? (ch.channel_position * 100).toFixed(0) + '%' : '—'}</span>
      </div>
    `;
  }

  // Entropy
  const entropy = data.gex_profile?.entropy || {};
  const entropyCls = entropy.regime === "CRITICAL" ? "critical" : entropy.regime === "APPROACHING" ? "warning" : "stable";
  const entropyHtml = entropy.regime && entropy.regime !== "DISPERSED" ? `
    <div class="regime-chip regime-chip--${entropyCls}">
      <span class="regime-chip__label">GEX Entropy</span>
      <span class="regime-chip__value">${entropy.entropy_norm?.toFixed(2) || "—"} (${entropy.regime})</span>
      <span class="regime-chip__sub">${entropy.description || ""}</span>
    </div>` : "";

  // Self-excitation
  const sei = acf.self_excitation || {};
  const seiHtml = sei.regime && sei.regime !== "NONE" && sei.regime !== "LOW_EXCITATION" ? `
    <div class="regime-chip regime-chip--${sei.regime === 'HIGH_EXCITATION' ? 'critical' : 'warning'}">
      <span class="regime-chip__label">Self-Excitation (Hawkes)</span>
      <span class="regime-chip__value">SEI = ${sei.sei?.toFixed(2) || "0"} (${sei.regime?.replace(/_/g, " ") || "—"})</span>
      <span class="regime-chip__sub">${sei.n_clusters || 0} clusters, avg ${sei.avg_cluster_size?.toFixed(1) || "0"} bars | ${sei.description || ""}</span>
    </div>` : "";

  // Beta-adjusted Re
  const betaAdjFactor = re.beta_adj_factor || 1.0;
  const reBetaAdj = re.number_beta_adj || re.number;
  const betaReNote = betaAdjFactor > 1.05
    ? ` | &beta;-adj: ${reBetaAdj.toFixed(2)} (${betaAdjFactor.toFixed(1)}x)`
    : "";

  $("#regime-strip").innerHTML = `
    <div class="regime-chip regime-chip--${gexCls}">
      <span class="regime-chip__label">GEX Regime</span>
      <span class="regime-chip__value">${gex === "POSITIVE_GAMMA" ? "Positive Gamma" : "Negative Gamma"}</span>
      <span class="regime-chip__sub">${gex === "POSITIVE_GAMMA" ? "Dealers stabilize" : "Dealers amplify"}</span>
    </div>
    <div class="regime-chip regime-chip--${acfCls}">
      <span class="regime-chip__label">ACF Regime</span>
      <span class="regime-chip__value">${acf.mean_acf1 < -0.05 ? "Long Gamma (Dampened)" : acf.mean_acf1 > 0.05 ? "Short Gamma (Amplified)" : "Neutral"}</span>
      <span class="regime-chip__sub">ACF1: ${acf.mean_acf1.toFixed(3)} | ${acf.pct_dampened}% damp / ${acf.pct_amplified}% amp | ${acf.stability}</span>
    </div>
    <div class="regime-chip regime-chip--${reCls}">
      <span class="regime-chip__label">Reynolds Number</span>
      <span class="regime-chip__value">Re = ${re.number.toFixed(2)} (${re.regime})</span>
      <span class="regime-chip__sub">C/P: ${re.call_put_ratio.toFixed(1)}x${betaReNote} | ${re.regime === "LAMINAR" ? "Walls hold" : re.regime === "TURBULENT" ? "Walls break" : "Could go either way"}</span>
    </div>
    ${channelHtml}
    ${entropyHtml}
    ${seiHtml}
    ${ph.warning ? `<div class="regime-chip regime-chip--warning"><span class="regime-chip__label">Phase Alert</span><span class="regime-chip__value">${ph.regime}</span><span class="regime-chip__sub">${ph.warning}</span></div>` : ""}
    ${_renderVolRegimeChips(data)}
  `;
}

/* ---- Technicals Strip ---- */
function renderTechnicals(data) {
  const t = data.technicals;
  const container = $("#technicals-strip");
  if (!t || !t.trend) {
    container.innerHTML = "";
    return;
  }

  const ma = t.moving_averages;
  const atr = t.atr;
  const rs = t.relative_strength;
  const vwap = t.vwap || {};
  const trend = t.trend;
  const tc = data.directional?.tech_context || {};

  const trendCls = trend.trend_score >= 2 ? "bullish" : trend.trend_score >= 1 ? "lean-bull" : trend.trend_score <= -2 ? "bearish" : trend.trend_score <= -1 ? "lean-bear" : "neutral";

  const rsCls = rs.rs_label === "STRONG_LEADER" || rs.rs_label === "OUTPERFORMING" ? "strong" : rs.rs_label === "STRONG_LAGGARD" || rs.rs_label === "UNDERPERFORMING" ? "weak" : "neutral";

  const maChips = ["sma_20", "sma_50", "sma_200"]
    .filter((k) => ma[k])
    .map((k) => {
      const m = ma[k];
      const label = k.replace("sma_", "");
      const posCls = m.position === "ABOVE" ? "above" : "below";
      return `<span class="tech-ma-chip tech-ma-chip--${posCls}">${label}: $${formatPrice(m.value)} (${m.distance_pct > 0 ? "+" : ""}${m.distance_pct}%)</span>`;
    })
    .join("");

  const confirmHtml = tc.confirms_thesis
    ? `<span class="tech-confirm tech-confirm--yes">Technicals CONFIRM thesis</span>`
    : tc.conflicts_thesis
    ? `<span class="tech-confirm tech-confirm--no">Technicals CONFLICT with thesis</span>`
    : `<span class="tech-confirm tech-confirm--neutral">Technicals neutral</span>`;

  container.innerHTML = `
    <div class="tech-chip tech-chip--trend tech-chip--${trendCls}">
      <span class="tech-chip__label">Trend</span>
      <span class="tech-chip__value">${trend.trend_label.replace(/_/g, " ")}</span>
      <span class="tech-chip__sub">${trend.trend_desc}</span>
    </div>
    <div class="tech-chip tech-chip--ma">
      <span class="tech-chip__label">MA Alignment</span>
      <span class="tech-chip__value">${ma.alignment_label?.replace(/_/g, " ") || "—"}</span>
      <div class="tech-chip__mas">${maChips}</div>
      ${ma.cross ? `<span class="tech-chip__cross">${ma.cross === "GOLDEN_CROSS_RECENT" ? "Golden Cross forming" : "Death Cross forming"}</span>` : ""}
    </div>
    <div class="tech-chip tech-chip--rs tech-chip--rs-${rsCls}">
      <span class="tech-chip__label">Relative Strength vs ${rs.benchmark || "SPY"}</span>
      <span class="tech-chip__value">${rs.rs_label?.replace(/_/g, " ") || "—"}</span>
      <span class="tech-chip__sub">5d: ${rs.rs_5d > 0 ? "+" : ""}${rs.rs_5d}% | 20d: ${rs.rs_20d > 0 ? "+" : ""}${rs.rs_20d}% | Trend: ${rs.rs_trend || "—"}</span>
    </div>
    <div class="tech-chip tech-chip--atr">
      <span class="tech-chip__label">ATR(14)</span>
      <span class="tech-chip__value">$${atr.atr?.toFixed(2) || "0"} (${atr.atr_pct?.toFixed(1) || "0"}%)</span>
      <span class="tech-chip__sub">5d range: ${atr.recent_range_pct?.toFixed(1) || "0"}% | ${atr.atr_trend || "—"}</span>
    </div>
    ${_renderVwapChip(vwap)}
    ${_renderBetaChip(rs)}
    <div class="tech-chip tech-chip--confirm">
      ${confirmHtml}
    </div>
  `;
}

function _renderVwapChip(vwap) {
  const v20 = vwap.vwap_20d;
  if (!v20) return "";
  const ctx = vwap.context || "N/A";
  const ctxCls = ctx === "ABOVE" || ctx === "EXTENDED_ABOVE" ? "above" : ctx === "BELOW" || ctx === "EXTENDED_BELOW" ? "below" : "neutral";
  return `
    <div class="tech-chip tech-chip--vwap tech-chip--vwap-${ctxCls}">
      <span class="tech-chip__label">VWAP (20d)</span>
      <span class="tech-chip__value">$${formatPrice(v20.value)} (${v20.distance_pct > 0 ? "+" : ""}${v20.distance_pct}%)</span>
      <span class="tech-chip__sub">${ctx.replace(/_/g, " ")} · Bands: $${formatPrice(v20.lower_1)}–$${formatPrice(v20.upper_1)}</span>
    </div>`;
}

function _renderBetaChip(rs) {
  const beta = rs?.beta_60d;
  if (!beta || beta === 1.0) return "";
  const adj = rs.beta_adj_factor || 1.0;
  const cls = beta < 0.7 ? "low" : beta > 1.5 ? "high" : "normal";
  const desc = beta < 0.7 ? `Low beta — gamma squeeze amplifier (${adj.toFixed(1)}x)` :
               beta > 1.5 ? "High beta — moves dampened relative to gamma" :
               "Normal beta range";
  return `
    <div class="tech-chip tech-chip--beta tech-chip--beta-${cls}">
      <span class="tech-chip__label">Beta (60d)</span>
      <span class="tech-chip__value">${beta.toFixed(2)}</span>
      <span class="tech-chip__sub">${desc}</span>
    </div>`;
}

/* ---- Key Levels ---- */
function renderKeyLevels(data) {
  const levels = data.key_levels;
  const distances = data.distances;
  const ch = data.channel;

  const items = [
    { key: "flip_point", label: "GEX Flip Point", value: levels.flip_point, cls: "flip" },
    { key: "max_pain", label: "Max Pain", value: levels.max_pain, cls: "max-pain" },
    { key: "abs_gamma_strike", label: "Abs Gamma Strike", value: levels.abs_gamma_strike, cls: "ags" },
    { key: "call_wall", label: "Call Wall", value: levels.call_wall?.strike, cls: "call-wall", extra: levels.call_wall?.oi ? `OI: ${formatOI(levels.call_wall.oi)}` : "" },
    { key: "put_wall", label: "Put Wall", value: levels.put_wall?.strike, cls: "put-wall", extra: levels.put_wall?.oi ? `OI: ${formatOI(levels.put_wall.oi)}` : "" },
  ];

  if (ch.floor) items.push({ key: "channel_floor", label: "Channel Floor", value: ch.floor, cls: "ch-floor" });
  if (ch.ceiling) items.push({ key: "channel_ceiling", label: "Channel Ceiling", value: ch.ceiling, cls: "ch-ceiling" });

  $("#levels-grid").innerHTML = items
    .map((item) => {
      const dist = distances[item.key];
      const distText = dist ? `${dist.distance_pct}% ${dist.side}` : "";
      return `
        <div class="level-item level-item--${item.cls}">
          <span class="level-item__label">${item.label}</span>
          <span class="level-item__value">$${formatPrice(item.value)}</span>
          <span class="level-item__distance">${distText}${item.extra ? ` &middot; ${item.extra}` : ""}</span>
        </div>`;
    })
    .join("");
}

/* ---- Positions (Buy-Only) ---- */
function renderPositions(data) {
  const positions = data.directional.positions;
  const container = $("#positions-container");

  if (!positions.length) {
    container.innerHTML = `<p style="color: var(--text-muted); padding: 20px;">No positions to recommend right now.</p>`;
    return;
  }

  container.innerHTML = positions
    .map((p) => {
      if (p.type === "skip") {
        return `
          <div class="pos-card pos-card--skip">
            <div class="pos-card__name">${p.name}</div>
            <div class="pos-card__edge">${p.edge}</div>
          </div>`;
      }

      const edgeCls = p.edge_type === "WITH_DEALER" ? "with" : p.edge_type === "AGAINST_DEALER" ? "against" : "transitional";
      const confCls = p.confidence === "HIGH" ? "high" : p.confidence === "MEDIUM" ? "medium" : "low";
      const dirCls = p.option_type === "CALL" ? "call" : p.option_type === "PUT" ? "put" : "neutral";

      const ve = data.vol_analysis?.vol_edge || {};
      const ivhvR = data.vol_analysis?.iv_hv?.iv_hv_ratio || 1;
      const skewR = data.vol_analysis?.skew?.regime || "";
      let volNote = "";
      if (ve.verdict === "AVOID_BUYING" || ve.verdict === "EXPENSIVE_VOL") {
        volNote = `<div class="pos-card__vol-warn">IV is ${Math.round(ivhvR * 100)}% of realized vol — consider debit spreads instead of naked ${p.option_type === "CALL" ? "calls" : "puts"}</div>`;
      } else if (ve.verdict === "NEUTRAL_VOL") {
        volNote = `<div class="pos-card__vol-note">IV slightly elevated (${ivhvR}x HV) — spreads reduce cost basis</div>`;
      } else if (ve.verdict === "STRONG_BUY_VOL" || ve.verdict === "BUY_VOL") {
        const cheapNote = ivhvR < 0.9 ? "cheap" : "fair";
        volNote = `<div class="pos-card__vol-good">Options are ${cheapNote} (IV/HV ${ivhvR}) — good for naked longs</div>`;
      }
      // Skew-aware note
      let skewNote = "";
      if (p.option_type === "CALL" && skewR === "CALL_SKEW") {
        skewNote = `<div class="pos-card__vol-warn">Call skew detected — calls are relatively expensive</div>`;
      } else if (p.option_type === "PUT" && skewR === "HIGH_PUT_SKEW") {
        skewNote = `<div class="pos-card__vol-warn">Put skew elevated — puts are relatively expensive</div>`;
      } else if (p.option_type === "CALL" && skewR === "HIGH_PUT_SKEW") {
        skewNote = `<div class="pos-card__vol-good">Put skew means calls have relative vol edge</div>`;
      }

      return `
        <div class="pos-card pos-card--${dirCls}">
          <div class="pos-card__header">
            <div class="pos-card__name">${p.name}</div>
            <div class="pos-card__badges">
              <span class="pos-card__badge pos-card__badge--${edgeCls}">${p.edge_type === "WITH_DEALER" ? "WITH Dealer" : p.edge_type === "AGAINST_DEALER" ? "AGAINST Dealer" : "Transitional"}</span>
              <span class="pos-card__badge pos-card__badge--${confCls}">${p.confidence}</span>
            </div>
          </div>
          <div class="pos-card__details">
            <div class="pos-card__detail">
              <span class="pos-card__detail-label">Strike</span>
              <span class="pos-card__detail-value pos-card__detail-value--mono">$${p.strike > 0 ? formatPrice(p.strike) : "—"}</span>
            </div>
            <div class="pos-card__detail">
              <span class="pos-card__detail-label">Type</span>
              <span class="pos-card__detail-value">${p.action} ${p.option_type}</span>
            </div>
            <div class="pos-card__detail">
              <span class="pos-card__detail-label">DTE</span>
              <span class="pos-card__detail-value">${p.dte_guidance}</span>
            </div>
            <div class="pos-card__detail">
              <span class="pos-card__detail-label">Sizing</span>
              <span class="pos-card__detail-value">${p.sizing}</span>
            </div>
            ${p.kelly_size ? `<div class="pos-card__detail">
              <span class="pos-card__detail-label">Kelly Size</span>
              <span class="pos-card__detail-value pos-card__detail-value--kelly">${p.kelly_size}</span>
            </div>` : ""}
            ${p.risk_dollars != null ? `<div class="pos-card__detail">
              <span class="pos-card__detail-label">Risk Budget</span>
              <span class="pos-card__detail-value pos-card__detail-value--kelly">$${formatLargeNumber(p.risk_dollars)}${p.max_contracts ? ` → ${p.max_contracts} contract${p.max_contracts > 1 ? "s" : ""}` : ""}${p.contract_cost ? ` ($${formatLargeNumber(p.contract_cost)}/ct)` : ""}</span>
            </div>` : ""}
            ${p.size_warning ? `<div class="pos-card__vol-warn">${p.size_warning}</div>` : ""}
          </div>
          <div class="pos-card__row">
            <span class="pos-card__row-label">Target</span>
            <span class="pos-card__row-value">${p.target}</span>
          </div>
          <div class="pos-card__row">
            <span class="pos-card__row-label">Stop / Exit</span>
            <span class="pos-card__row-value">${p.stop}</span>
          </div>
          <div class="pos-card__edge-text">${p.edge}</div>
          ${volNote}${skewNote}
        </div>`;
    })
    .join("");
}

/* ---- Straddle/Strangle Analysis ---- */
function renderStraddles(data) {
  const sa = data.straddle_analysis;
  const container = $("#straddle-container");
  if (!sa) {
    container.innerHTML = `<p style="color: var(--text-muted); padding: 20px;">Straddle analysis unavailable.</p>`;
    return;
  }

  const { straddle, strangle, iv_vs_rv, atr_context, score, verdict, verdict_label, reasoning, warnings, suggested_dte, suggested_sizing, vrp: straddleVrp } = sa;
  const ac = atr_context || {};

  const verdictCls = verdict === "BUY_STRADDLE" || verdict === "BUY_STRANGLE" ? "buy" : verdict === "CONSIDER" ? "consider" : "avoid";
  const verdictIcon = verdictCls === "buy" ? "+" : verdictCls === "consider" ? "~" : "×";

  const scoreBarHtml = (label, val, max = 25) => {
    const pct = Math.round((val / max) * 100);
    const cls = pct >= 70 ? "high" : pct >= 40 ? "medium" : "low";
    return `
      <div class="ss-score__row">
        <span class="ss-score__label">${label}</span>
        <div class="ss-score__bar">
          <div class="ss-score__fill ss-score__fill--${cls}" style="width: ${pct}%"></div>
        </div>
        <span class="ss-score__val">${val}/${max}</span>
      </div>`;
  };

  container.innerHTML = `
    <div class="ss-layout">
      <div class="ss-verdict ss-verdict--${verdictCls}">
        <div class="ss-verdict__icon">${verdictIcon}</div>
        <div class="ss-verdict__text">
          <div class="ss-verdict__label">${verdict.replace(/_/g, " ")}</div>
          <div class="ss-verdict__desc">${verdict_label}</div>
        </div>
        <div class="ss-verdict__score">${score.total}<span class="ss-verdict__score-max">/100</span></div>
      </div>

      <div class="ss-cards">
        <div class="ss-card">
          <div class="ss-card__title">ATM Straddle</div>
          <div class="ss-card__strike">Strike: $${formatPrice(straddle.strike)}</div>
          <div class="ss-card__grid">
            <div class="ss-card__item">
              <span class="ss-card__item-label">Call</span>
              <span class="ss-card__item-value">$${straddle.call_premium.toFixed(2)}</span>
            </div>
            <div class="ss-card__item">
              <span class="ss-card__item-label">Put</span>
              <span class="ss-card__item-value">$${straddle.put_premium.toFixed(2)}</span>
            </div>
            <div class="ss-card__item">
              <span class="ss-card__item-label">Total</span>
              <span class="ss-card__item-value ss-card__item-value--accent">$${straddle.total_cost.toFixed(2)}</span>
            </div>
            <div class="ss-card__item">
              <span class="ss-card__item-label">Per Contract</span>
              <span class="ss-card__item-value">$${straddle.total_cost_per_contract.toFixed(0)}</span>
            </div>
          </div>
          <div class="ss-card__breakevens">
            <div class="ss-card__be">
              <span class="ss-card__be-label">Lower BE</span>
              <span class="ss-card__be-value ss-card__be-value--red">$${formatPrice(straddle.lower_breakeven)}</span>
            </div>
            <div class="ss-card__be-center">${straddle.required_move_pct}% move needed</div>
            <div class="ss-card__be">
              <span class="ss-card__be-label">Upper BE</span>
              <span class="ss-card__be-value ss-card__be-value--green">$${formatPrice(straddle.upper_breakeven)}</span>
            </div>
          </div>
          ${straddle.call_iv ? `<div class="ss-card__iv">Call IV: ${straddle.call_iv}% | Put IV: ${straddle.put_iv}%</div>` : ""}
        </div>

        <div class="ss-card">
          <div class="ss-card__title">OTM Strangle</div>
          <div class="ss-card__strike">$${formatPrice(strangle.put_strike)} P / $${formatPrice(strangle.call_strike)} C (${strangle.width_pct?.toFixed(1) || "—"}% wide)</div>
          <div class="ss-card__grid">
            <div class="ss-card__item">
              <span class="ss-card__item-label">Call</span>
              <span class="ss-card__item-value">$${strangle.call_premium.toFixed(2)}</span>
            </div>
            <div class="ss-card__item">
              <span class="ss-card__item-label">Put</span>
              <span class="ss-card__item-value">$${strangle.put_premium.toFixed(2)}</span>
            </div>
            <div class="ss-card__item">
              <span class="ss-card__item-label">Total</span>
              <span class="ss-card__item-value ss-card__item-value--accent">$${strangle.total_cost.toFixed(2)}</span>
            </div>
            <div class="ss-card__item">
              <span class="ss-card__item-label">Per Contract</span>
              <span class="ss-card__item-value">$${strangle.total_cost_per_contract.toFixed(0)}</span>
            </div>
          </div>
          <div class="ss-card__breakevens">
            <div class="ss-card__be">
              <span class="ss-card__be-label">Lower BE</span>
              <span class="ss-card__be-value ss-card__be-value--red">$${formatPrice(strangle.lower_breakeven)}</span>
            </div>
            <div class="ss-card__be-center">${strangle.required_move_pct}% move needed</div>
            <div class="ss-card__be">
              <span class="ss-card__be-label">Upper BE</span>
              <span class="ss-card__be-value ss-card__be-value--green">$${formatPrice(strangle.upper_breakeven)}</span>
            </div>
          </div>
        </div>
      </div>

      <div class="ss-details ss-details--3col">
        <div class="ss-scores">
          <div class="ss-scores__title">Score Breakdown</div>
          ${scoreBarHtml("Regime", score.regime)}
          ${scoreBarHtml("IV Value", score.iv)}
          ${scoreBarHtml("Catalyst", score.catalyst)}
          ${scoreBarHtml("Structure", score.structural)}
          ${score.vrp_drag !== undefined && score.vrp_drag !== 0 ? `
            <div class="ss-score__row">
              <span class="ss-score__label">VRP Drag</span>
              <div class="ss-score__bar">
                <div class="ss-score__fill ss-score__fill--${score.vrp_drag > 0 ? 'high' : score.vrp_drag > -5 ? 'medium' : 'low'}" style="width: ${Math.abs(score.vrp_drag) * 4}%"></div>
              </div>
              <span class="ss-score__val" style="color: ${score.vrp_drag > 0 ? 'var(--green)' : 'var(--red)'}">${score.vrp_drag > 0 ? '+' : ''}${score.vrp_drag}</span>
            </div>` : ""}
        </div>

        <div class="ss-iv-box">
          <div class="ss-iv-box__title">IV vs Realized Vol</div>
          <div class="ss-iv-box__row">
            <span>ATM IV</span>
            <span class="ss-iv-box__val">${iv_vs_rv.atm_iv}%</span>
          </div>
          <div class="ss-iv-box__row">
            <span>Realized Vol</span>
            <span class="ss-iv-box__val">${iv_vs_rv.realized_vol}%</span>
          </div>
          <div class="ss-iv-box__row ss-iv-box__row--highlight">
            <span>IV/RV Ratio</span>
            <span class="ss-iv-box__val ss-iv-box__val--${iv_vs_rv.iv_context === 'CHEAP' ? 'green' : iv_vs_rv.iv_context === 'EXPENSIVE' ? 'red' : 'neutral'}">${iv_vs_rv.iv_rv_ratio} (${iv_vs_rv.iv_context})</span>
          </div>
        </div>

        <div class="ss-iv-box">
          <div class="ss-iv-box__title">ATR vs Breakeven</div>
          <div class="ss-iv-box__row">
            <span>Daily ATR</span>
            <span class="ss-iv-box__val">${ac.atr_pct || 0}%</span>
          </div>
          <div class="ss-iv-box__row">
            <span>Breakeven</span>
            <span class="ss-iv-box__val">${ac.breakeven_pct || 0}%</span>
          </div>
          <div class="ss-iv-box__row ss-iv-box__row--highlight">
            <span>ATR Coverage</span>
            <span class="ss-iv-box__val ss-iv-box__val--${ac.atr_coverage > 1.0 ? 'green' : ac.atr_coverage > 0.7 ? 'neutral' : 'red'}">${ac.atr_coverage || 0}x ${ac.atr_coverage > 1.0 ? '(1-day range covers BE)' : `(~${ac.days_to_breakeven || '?'}d to BE)`}</span>
          </div>
          <div class="ss-iv-box__guidance">
            <div><strong>DTE:</strong> ${suggested_dte}</div>
            <div><strong>Size:</strong> ${suggested_sizing}</div>
            ${sa.risk_dollars != null ? `<div><strong>Risk:</strong> $${formatLargeNumber(sa.risk_dollars)}${sa.max_contracts ? ` → ${sa.max_contracts} contract${sa.max_contracts > 1 ? "s" : ""}` : ""}${sa.contract_cost ? ` ($${formatLargeNumber(sa.contract_cost)}/ct)` : ""}</div>` : ""}
            ${sa.size_warning ? `<div style="color: var(--amber); font-size: 0.68rem; margin-top: 4px;">${sa.size_warning}</div>` : ""}
          </div>
        </div>
      </div>

      <div class="ss-reasoning">
        ${reasoning.map((r) => `<div class="ss-reason">${r}</div>`).join("")}
      </div>

      ${warnings.length ? `
        <div class="ss-warnings">
          ${warnings.map((w) => `<div class="ss-warning">${w}</div>`).join("")}
        </div>
      ` : ""}

      ${_renderMoveProbability(sa.move_probability)}
      ${_renderPnlScenarios(sa.pnl_scenarios, straddle)}
      ${_renderExpiryScan(data.expiry_scan)}
      ${_renderThetaSchedule(sa.theta_schedule, straddle)}
    </div>
  `;
}

function _renderMoveProbability(mp) {
  if (!mp || !mp.windows || !mp.windows.length) return "";
  const probCls = mp.probability >= 80 ? "green" : mp.probability >= 50 ? "neutral" : "red";
  return `
    <div class="ss-extra">
      <div class="ss-extra__title">Historical Move Probability</div>
      <div class="ss-extra__subtitle">How often did this stock move ≥ breakeven % over the last year?</div>
      <div class="ss-prob-grid">
        ${mp.windows.map((w) => {
          const cls = w.probability >= 80 ? "high" : w.probability >= 50 ? "mid" : "low";
          return `<div class="ss-prob-chip ss-prob-chip--${cls} ${w.is_current ? "ss-prob-chip--current" : ""}">
            <span class="ss-prob-chip__dte">${w.dte}d</span>
            <span class="ss-prob-chip__pct">${w.probability}%</span>
          </div>`;
        }).join("")}
      </div>
      <div class="ss-extra__note">Based on ${mp.sample_size} rolling windows. ${mp.probability >= 80 ? "Stock routinely moves enough." : mp.probability >= 50 ? "Moves this size happen about half the time." : "Stock rarely moves enough — risky entry."}</div>
    </div>`;
}

function _renderPnlScenarios(scenarios, straddle) {
  if (!scenarios || !scenarios.length) return "";
  return `
    <div class="ss-extra">
      <div class="ss-extra__title">P/L at Key Levels</div>
      <div class="ss-extra__subtitle">Straddle P/L if price reaches each dealer level (ignoring IV/theta changes).</div>
      <div class="ss-pnl-table">
        <div class="ss-pnl-header">
          <span>Level</span><span>Price</span><span>Move</span><span>P/L</span><span>Return</span>
        </div>
        ${scenarios.map((s) => `
          <div class="ss-pnl-row ${s.profitable ? "ss-pnl-row--profit" : "ss-pnl-row--loss"}">
            <span class="ss-pnl-row__label">${s.label}</span>
            <span class="ss-pnl-row__mono">$${formatPrice(s.price)}</span>
            <span class="ss-pnl-row__mono">${s.move_pct}%</span>
            <span class="ss-pnl-row__mono ss-pnl-row__${s.profitable ? "green" : "red"}">$${s.pnl > 0 ? "+" : ""}${s.pnl.toFixed(2)}</span>
            <span class="ss-pnl-row__mono ss-pnl-row__${s.profitable ? "green" : "red"}">${s.pnl_pct > 0 ? "+" : ""}${s.pnl_pct}%</span>
          </div>`).join("")}
      </div>
    </div>`;
}

function _renderExpiryScan(scan) {
  if (!scan || !scan.expirations || !scan.expirations.length) return "";
  const best = scan.best;
  return `
    <div class="ss-extra">
      <div class="ss-extra__title">Expiration Comparison</div>
      <div class="ss-extra__subtitle">Straddle cost and edge across available expirations.${best ? ` Best: <strong>${best.expiration} (${best.dte}d)</strong>` : ""}</div>
      <div class="ss-pnl-table ss-pnl-table--7col">
        <div class="ss-pnl-header">
          <span>Expiry</span><span>DTE</span><span>Cost</span><span>BE %</span><span>IV/RV</span><span>ATR Cov</span><span>Score</span>
        </div>
        ${scan.expirations.map((e) => `
          <div class="ss-pnl-row ${best && e.expiration === best.expiration ? "ss-pnl-row--best" : ""} ${e.is_current ? "ss-pnl-row--current" : ""}">
            <span class="ss-pnl-row__label">${e.expiration}</span>
            <span class="ss-pnl-row__mono">${e.dte}d</span>
            <span class="ss-pnl-row__mono">$${e.cost.toFixed(2)}</span>
            <span class="ss-pnl-row__mono">${e.breakeven_pct.toFixed(1)}%</span>
            <span class="ss-pnl-row__mono ss-pnl-row__${e.iv_rv_ratio < 0.85 ? "green" : e.iv_rv_ratio > 1.15 ? "red" : "neutral"}">${e.iv_rv_ratio.toFixed(2)}</span>
            <span class="ss-pnl-row__mono ss-pnl-row__${e.atr_coverage > 1 ? "green" : e.atr_coverage > 0.7 ? "neutral" : "red"}">${e.atr_coverage}x</span>
            <span class="ss-pnl-row__mono"><strong>${e.score}</strong></span>
          </div>`).join("")}
      </div>
    </div>`;
}

function _renderThetaSchedule(theta, straddle) {
  if (!theta || !theta.schedule || !theta.schedule.length) return "";
  return `
    <div class="ss-extra">
      <div class="ss-extra__title">Theta Decay</div>
      <div class="ss-extra__subtitle">Daily cost of holding. Premium half-life: day ${theta.half_life_day}. Day 1 theta: $${theta.daily_theta}/share (${theta.daily_theta_pct}% of premium).</div>
      <div class="ss-theta-bar">
        ${theta.schedule.map((s) => {
          const h = Math.max(4, Math.min(40, s.cumulative_decay_pct * 0.4));
          return `<div class="ss-theta-col" title="Day ${s.day}: $${s.theta}/day, ${s.cumulative_decay_pct}% decayed">
            <div class="ss-theta-fill" style="height: ${h}px"></div>
            <span class="ss-theta-day">${s.day}</span>
          </div>`;
        }).join("")}
      </div>
    </div>`;
}

/* ---- Volatility Edge ---- */
function renderVolAnalysis(data) {
  const va = data.vol_analysis;
  const container = $("#vol-analysis-container");
  if (!va) {
    container.innerHTML = `<p style="color: var(--text-muted)">Vol analysis unavailable.</p>`;
    return;
  }

  const ivhv = va.iv_hv || {};
  const skew = va.skew || {};
  const term = va.term_structure || {};
  const vrp = va.vrp || {};
  const edge = va.vol_edge || {};

  const edgeCls = edge.score >= 60 ? "buy" : edge.score >= 40 ? "consider" : edge.score >= 25 ? "neutral" : "avoid";
  const edgeIcon = edge.score >= 60 ? "+" : edge.score >= 40 ? "~" : edge.score >= 25 ? "—" : "×";

  const ivhvCls = ivhv.context === "CHEAP" || ivhv.context === "SLIGHT_DISCOUNT" ? "green" :
                  ivhv.context === "EXPENSIVE" || ivhv.context === "VERY_EXPENSIVE" ? "red" : "neutral";

  const skewCls = skew.regime === "HIGH_PUT_SKEW" ? "warning" :
                  skew.regime === "CALL_SKEW" || skew.regime === "EXTREME_CALL_SKEW" ? "negative" : "neutral";

  const termCls = term.shape === "CONTANGO" ? "green" :
                  term.shape === "BACKWARDATION" ? "red" :
                  term.shape === "MILD_BACKWARDATION" ? "warning" : "neutral";

  const termLabel = term.shape ? term.shape.replace(/_/g, " ") : "—";

  // Mini gauge for IV/HV ratio
  const ratioPos = Math.min(100, Math.max(0, ((ivhv.iv_hv_ratio || 1) - 0.5) / 1.5 * 100));

  // Term structure mini chart
  const termPoints = (term.points || []);
  let termChartHtml = "";
  if (termPoints.length >= 2) {
    const maxIv = Math.max(...termPoints.map(p => p.atm_iv));
    const minIv = Math.min(...termPoints.map(p => p.atm_iv));
    const range = maxIv - minIv || 1;
    termChartHtml = `
      <div class="va-term-chart">
        ${termPoints.map((p, i) => {
          const h = 20 + ((p.atm_iv - minIv) / range) * 40;
          return `<div class="va-term-bar" style="height: ${h}px" title="${p.expiration} (${p.dte}d): ${p.atm_iv}%">
            <span class="va-term-bar__iv">${p.atm_iv}%</span>
            <span class="va-term-bar__dte">${p.dte}d</span>
          </div>`;
        }).join("")}
      </div>`;
  }

  container.innerHTML = `
    <div class="va-layout">
      <div class="va-verdict va-verdict--${edgeCls}">
        <div class="va-verdict__icon">${edgeIcon}</div>
        <div class="va-verdict__text">
          <div class="va-verdict__label">${edge.verdict?.replace(/_/g, " ") || "—"}</div>
          <div class="va-verdict__desc">${edge.label || ""}</div>
        </div>
        <div class="va-verdict__score">${edge.score}<span class="va-verdict__score-max">/100</span></div>
      </div>

      <div class="va-cards">
        <div class="va-card">
          <div class="va-card__title">IV vs Realized Vol</div>
          <div class="va-card__row">
            <span>ATM IV</span>
            <span class="va-card__val">${ivhv.atm_iv || 0}%</span>
          </div>
          <div class="va-card__row">
            <span>HV (${ivhv.hv_window || "20d"})</span>
            <span class="va-card__val">${ivhv.hv_used || 0}%</span>
          </div>
          <div class="va-card__hv-detail">
            <span>10d: ${ivhv.hv_10d || 0}%</span>
            <span>20d: ${ivhv.hv_20d || 0}%</span>
            <span>30d: ${ivhv.hv_30d || 0}%</span>
            <span>60d: ${ivhv.hv_60d || 0}%</span>
          </div>
          <div class="va-card__row va-card__row--highlight">
            <span>IV/HV Ratio</span>
            <span class="va-card__val va-card__val--${ivhvCls}">${ivhv.iv_hv_ratio || "—"}</span>
          </div>
          <div class="va-gauge">
            <div class="va-gauge__track">
              <div class="va-gauge__zone va-gauge__zone--cheap" style="width: 27%"></div>
              <div class="va-gauge__zone va-gauge__zone--fair" style="width: 20%"></div>
              <div class="va-gauge__zone va-gauge__zone--expensive" style="width: 53%"></div>
              <div class="va-gauge__marker" style="left: ${ratioPos}%"></div>
            </div>
            <div class="va-gauge__labels">
              <span>Cheap</span><span>Fair</span><span>Expensive</span>
            </div>
          </div>
          <div class="va-card__context">${ivhv.label || ""}</div>
          <div class="va-card__row" style="margin-top: 4px">
            <span>HV Percentile</span>
            <span class="va-card__val">${ivhv.iv_percentile_proxy || 50}th</span>
          </div>
        </div>

        <div class="va-card">
          <div class="va-card__title">Put/Call Skew</div>
          <div class="va-card__row">
            <span>OTM Put IV</span>
            <span class="va-card__val">${skew.otm_put_iv || 0}%</span>
          </div>
          <div class="va-card__row">
            <span>OTM Call IV</span>
            <span class="va-card__val">${skew.otm_call_iv || 0}%</span>
          </div>
          <div class="va-card__row va-card__row--highlight">
            <span>Risk Reversal</span>
            <span class="va-card__val va-card__val--${skewCls}">${skew.risk_reversal > 0 ? "+" : ""}${skew.risk_reversal || 0} pts</span>
          </div>
          <div class="va-card__regime va-card__regime--${skewCls}">${skew.regime?.replace(/_/g, " ") || "—"}</div>
          <div class="va-card__context">${skew.description || ""}</div>
          <div class="va-card__trade">${skew.trade_implication || ""}</div>
        </div>

        <div class="va-card">
          <div class="va-card__title">Term Structure</div>
          ${termChartHtml}
          <div class="va-card__row va-card__row--highlight" style="margin-top: 8px">
            <span>Shape</span>
            <span class="va-card__val va-card__val--${termCls}">${termLabel}</span>
          </div>
          ${term.front_iv ? `
            <div class="va-card__row">
              <span>Front IV</span>
              <span class="va-card__val">${term.front_iv}%</span>
            </div>
            <div class="va-card__row">
              <span>Back IV</span>
              <span class="va-card__val">${term.back_iv}%</span>
            </div>
            <div class="va-card__row">
              <span>Slope</span>
              <span class="va-card__val">${term.slope > 0 ? "+" : ""}${term.slope} pts</span>
            </div>
          ` : ""}
          <div class="va-card__context">${term.description || ""}</div>
          <div class="va-card__trade">${term.trade_implication || ""}</div>
        </div>

        ${vrp.context && vrp.context !== "N/A" ? `
        <div class="va-card va-card--vrp">
          <div class="va-card__title">Variance Risk Premium</div>
          <div class="va-card__row">
            <span>Raw VRP</span>
            <span class="va-card__val">${vrp.vrp_raw > 0 ? "+" : ""}${vrp.vrp_raw} var pts</span>
          </div>
          <div class="va-card__row">
            <span>GEX-Adjusted VRP</span>
            <span class="va-card__val va-card__val--${vrp.context === "DISCOUNT" ? "green" : vrp.context === "HIGH_PREMIUM" ? "red" : vrp.context === "MODERATE_PREMIUM" ? "warning" : "neutral"}">${vrp.vrp_gex_adjusted > 0 ? "+" : ""}${vrp.vrp_gex_adjusted}</span>
          </div>
          <div class="va-card__row">
            <span>GEX-Implied HV</span>
            <span class="va-card__val">${vrp.gex_implied_hv}% (${vrp.gex_vol_mult}x)</span>
          </div>
          <div class="va-card__row">
            <span>Daily VRP Drag</span>
            <span class="va-card__val">${vrp.daily_vrp_drag > 0 ? "-" : "+"}${Math.abs(vrp.daily_vrp_drag).toFixed(3)}/day</span>
          </div>
          <div class="va-card__context">${vrp.label || ""}</div>
        </div>
        ` : ""}
      </div>

      ${edge.factors && edge.factors.length ? `
        <div class="va-factors">
          ${edge.factors.map(f => `<div class="va-factor">${f}</div>`).join("")}
        </div>
      ` : ""}
    </div>
  `;
}

/* ---- Collision Timeline (ranked table) ---- */
function renderCollisionTimeline(data) {
  const cts = data.collision_times;
  const container = $("#collision-timeline");
  if (!container) return;
  if (!cts || !cts.length) {
    container.innerHTML = "";
    return;
  }

  const nameMap = {
    call_wall: "Call Wall", put_wall: "Put Wall", max_pain: "Max Pain",
    flip_point: "Flip Point", abs_gamma_strike: "Gamma Strike",
    channel_floor: "Ch Floor", channel_ceiling: "Ch Ceiling",
  };
  const colorMap = {
    call_wall: "#ef4444", put_wall: "#22c55e", max_pain: "#6366f1",
    flip_point: "#f59e0b", abs_gamma_strike: "#06b6d4",
    channel_floor: "#22c55e", channel_ceiling: "#ef4444",
  };

  const regimeMult = cts[0]?.regime_mult;
  const regimeNote = regimeMult && regimeMult !== 1.0
    ? `Regime adjustment: ${regimeMult < 1 ? "faster (turbulent)" : "slower (dampened)"} (${regimeMult}x)`
    : "No regime adjustment";

  const rows = cts.map((c) => {
    const name = nameMap[c.level_label] || c.level_label;
    const color = colorMap[c.level_label] || "#6366f1";
    const urgCls = c.urgency === "NOW" ? "now" : c.urgency === "IMMINENT" ? "imminent" : c.urgency === "SOON" ? "soon" : "far";
    const etaText = c.expected_days_adj < 0.5 ? "<1d" : `~${c.expected_days_adj}d`;
    const dir = c.side === "above" ? "↑" : "↓";
    const probBarW = Math.min(Math.max(c.prob_within_dte, 2), 100);
    const probCls = c.prob_within_dte >= 70 ? "high" : c.prob_within_dte >= 40 ? "mid" : "low";

    return `
      <div class="ct-row ct-row--${urgCls}">
        <span class="ct-row__urgency">${c.urgency}</span>
        <span class="ct-row__name" style="color: ${color}">${dir} ${name}</span>
        <span class="ct-row__price">$${formatPrice(c.level_price)}</span>
        <span class="ct-row__dist">${c.distance_pct}%</span>
        <span class="ct-row__eta">${etaText}</span>
        <span class="ct-row__prob">
          <span class="ct-row__prob-bar"><span class="ct-row__prob-fill ct-row__prob-fill--${probCls}" style="width:${probBarW}%"></span></span>
          <span class="ct-row__prob-val">${c.prob_within_dte}%</span>
        </span>
      </div>`;
  }).join("");

  container.innerHTML = `
    <div class="ct-card">
      <div class="ct-card__header">
        <span class="ct-card__title">Time to Key Levels</span>
        <span class="ct-card__note">${regimeNote} &middot; ${data.dte} DTE</span>
      </div>
      <div class="ct-card__labels">
        <span></span><span>Level</span><span>Price</span><span>Dist</span><span>ETA</span><span>Reach within DTE</span>
      </div>
      ${rows}
    </div>
  `;
}

/* ---- Level Actions ---- */
function renderLevelActions(data) {
  const actions = data.directional.level_actions;
  if (!actions || !actions.length) {
    $("#level-actions").innerHTML = `<p style="color: var(--text-muted);">No level data available.</p>`;
    return;
  }

  const typeColors = {
    max_pain: "#6366f1",
    call_wall: "#ef4444",
    put_wall: "#22c55e",
    flip_point: "#f59e0b",
    abs_gamma_strike: "#06b6d4",
    channel_floor: "#22c55e",
    channel_ceiling: "#ef4444",
  };

  // Build a lookup for collision times
  const ctMap = {};
  (data.collision_times || []).forEach((ct) => { ctMap[ct.level_price?.toFixed(2)] = ct; });

  $("#level-actions").innerHTML = actions
    .map((a) => {
      const color = typeColors[a.type] || "#6366f1";
      const ct = ctMap[a.level?.toFixed(2)] || {};
      const colProb = a.collision_prob;
      const colLabel = a.collision_label;
      const colCls = colLabel === "LIKELY" ? "high" : colLabel === "POSSIBLE" ? "mid" : "low";
      const etaDays = ct.expected_days_adj;
      const etaProb = ct.prob_within_dte;
      const urgency = ct.urgency;
      const urgCls = urgency === "NOW" || urgency === "IMMINENT" ? "high" : urgency === "SOON" ? "mid" : "low";

      return `
        <div class="la-card" style="border-left-color: ${color};">
          <div class="la-card__header">
            <span class="la-card__level" style="color: ${color};">$${formatPrice(a.level)}</span>
            <span class="la-card__label">${a.label}</span>
            <span class="la-card__dist">${a.distance_pct}% ${a.side}</span>
            ${colProb !== undefined ? `<span class="la-card__collision la-card__collision--${colCls}">${colProb}% reach</span>` : ""}
          </div>
          ${etaDays !== undefined ? `<div class="la-card__eta">
            <span class="la-card__eta-badge la-card__eta-badge--${urgCls}">${urgency}</span>
            ~${etaDays} days to reach &middot; ${etaProb}% within DTE
          </div>` : ""}
          <div class="la-card__expect">${a.expectation}</div>
          <div class="la-card__action">${a.action}</div>
          <div class="la-card__watch">${a.watch_for}</div>
        </div>`;
    })
    .join("");
}

/* ---- GEX Chart (SVG) ---- */
function renderGexChart(data) {
  const strikes = data.gex_profile.by_strike;
  if (!strikes.length) return;

  renderGexChartFromData(strikes, data.spot, {
    max_pain: data.key_levels.max_pain,
    flip_point: data.key_levels.flip_point,
    abs_gamma_strike: data.key_levels.abs_gamma_strike,
    call_wall: data.key_levels.call_wall,
    put_wall: data.key_levels.put_wall,
  }, data.channel);
}

function renderGexChartFromData(strikes, spot, keyLevels, ch) {
  if (!strikes || !strikes.length) return;

  const range = spot * 0.12;
  const filtered = strikes.filter((s) => Math.abs(s.strike - spot) <= range);
  if (!filtered.length) return;

  const margin = { top: 30, right: 20, bottom: 50, left: 70 };
  const width = 700;
  const height = 320;
  const innerW = width - margin.left - margin.right;
  const innerH = height - margin.top - margin.bottom;

  const maxAbs = Math.max(...filtered.map((s) => Math.abs(s.net_gex)), 1);
  const barWidth = Math.max(Math.floor(innerW / filtered.length) - 2, 3);
  const totalBarsWidth = (barWidth + 2) * filtered.length;

  const xScale = (i) => margin.left + (innerW - totalBarsWidth) / 2 + i * (barWidth + 2);
  const yScale = (val) => margin.top + innerH / 2 - (val / maxAbs) * (innerH / 2);

  let svg = `<svg viewBox="0 0 ${width} ${height}" width="100%" preserveAspectRatio="xMidYMid meet">`;

  if (ch && ch.floor && ch.ceiling) {
    const floorIdx = filtered.findIndex((s) => s.strike >= ch.floor);
    const ceilIdx = filtered.findIndex((s) => s.strike >= ch.ceiling);
    if (floorIdx >= 0 && ceilIdx >= 0) {
      const fx = xScale(floorIdx);
      const cx = xScale(ceilIdx) + barWidth;
      svg += `<rect x="${fx}" y="${margin.top}" width="${cx - fx}" height="${innerH}" fill="rgba(99,102,241,0.06)" rx="4"/>`;
    }
  }

  svg += `<line x1="${margin.left}" y1="${yScale(0)}" x2="${width - margin.right}" y2="${yScale(0)}" class="chart-zero"/>`;

  filtered.forEach((s, i) => {
    const x = xScale(i);
    const barH = Math.abs(s.net_gex / maxAbs) * (innerH / 2);
    const y = s.net_gex >= 0 ? yScale(0) - barH : yScale(0);
    const cls = s.net_gex >= 0 ? "bar-positive" : "bar-negative";
    svg += `<rect x="${x}" y="${y}" width="${barWidth}" height="${Math.max(barH, 1)}" class="${cls}" rx="1">
      <title>Strike: ${s.strike}\nGEX: ${formatLargeNumber(s.net_gex)}\nCall OI: ${formatOI(s.call_oi)}\nPut OI: ${formatOI(s.put_oi)}</title>
    </rect>`;
  });

  const labelInterval = Math.max(Math.floor(filtered.length / 12), 1);
  filtered.forEach((s, i) => {
    if (i % labelInterval === 0) {
      svg += `<text x="${xScale(i) + barWidth / 2}" y="${height - 8}" text-anchor="middle" class="chart-label">${s.strike}</text>`;
    }
  });

  const yTicks = [-maxAbs, -maxAbs / 2, 0, maxAbs / 2, maxAbs];
  yTicks.forEach((t) => {
    svg += `<text x="${margin.left - 8}" y="${yScale(t) + 3}" text-anchor="end" class="chart-label">${formatLargeNumber(t)}</text>`;
  });

  const spotIdx = filtered.findIndex((s) => s.strike >= spot);
  if (spotIdx >= 0) {
    const spotX = xScale(spotIdx) + barWidth / 2;
    svg += `<line x1="${spotX}" y1="${margin.top}" x2="${spotX}" y2="${height - margin.bottom}" class="chart-spot-line"/>`;
    svg += `<text x="${spotX}" y="${margin.top - 8}" text-anchor="middle" class="chart-spot-label">SPOT $${formatPrice(spot)}</text>`;
  }

  const levelMarkers = [
    { value: keyLevels.max_pain, label: "MP", color: "#6366f1" },
    { value: keyLevels.flip_point, label: "FLIP", color: "#f59e0b" },
    { value: keyLevels.call_wall?.strike, label: "CW", color: "#ef4444" },
    { value: keyLevels.put_wall?.strike, label: "PW", color: "#22c55e" },
  ];
  if (ch) {
    levelMarkers.push({ value: ch.floor, label: "FL", color: "#22c55e" });
    levelMarkers.push({ value: ch.ceiling, label: "CL", color: "#ef4444" });
  }

  levelMarkers.forEach((lm) => {
    if (!lm.value) return;
    const idx = filtered.findIndex((s) => s.strike >= lm.value);
    if (idx < 0) return;
    const lx = xScale(idx) + barWidth / 2;
    svg += `<line x1="${lx}" y1="${margin.top}" x2="${lx}" y2="${height - margin.bottom}" class="chart-level-line" stroke="${lm.color}"/>`;
    svg += `<text x="${lx}" y="${height - margin.bottom + 24}" text-anchor="middle" class="chart-level-label" fill="${lm.color}">${lm.label}</text>`;
  });

  svg += "</svg>";
  $("#gex-chart").innerHTML = svg;
}

/* ---- OI Chart ---- */
function renderOIChart(data) {
  const strikes = data.gex_profile.by_strike;
  if (!strikes.length) return;

  const spot = data.spot;
  const range = spot * 0.12;
  const filtered = strikes.filter((s) => Math.abs(s.strike - spot) <= range);
  if (!filtered.length) return;

  const margin = { top: 20, right: 20, bottom: 50, left: 70 };
  const width = 900;
  const height = 280;
  const innerW = width - margin.left - margin.right;
  const innerH = height - margin.top - margin.bottom;

  const maxOI = Math.max(...filtered.map((s) => Math.max(s.call_oi, s.put_oi)), 1);
  const barW = Math.max(Math.floor(innerW / filtered.length / 2) - 1, 2);
  const groupW = barW * 2 + 3;
  const totalGroupW = groupW * filtered.length;

  const xScale = (i) => margin.left + (innerW - totalGroupW) / 2 + i * groupW;
  const yScale = (val) => margin.top + innerH - (val / maxOI) * innerH;

  let svg = `<svg viewBox="0 0 ${width} ${height}" width="100%" preserveAspectRatio="xMidYMid meet">`;

  filtered.forEach((s, i) => {
    const x = xScale(i);
    const callH = (s.call_oi / maxOI) * innerH;
    const putH = (s.put_oi / maxOI) * innerH;
    svg += `<rect x="${x}" y="${yScale(s.call_oi)}" width="${barW}" height="${Math.max(callH, 0.5)}" class="bar-call-oi" rx="1"><title>Strike ${s.strike} - Call OI: ${formatOI(s.call_oi)}</title></rect>`;
    svg += `<rect x="${x + barW + 1}" y="${yScale(s.put_oi)}" width="${barW}" height="${Math.max(putH, 0.5)}" class="bar-put-oi" rx="1"><title>Strike ${s.strike} - Put OI: ${formatOI(s.put_oi)}</title></rect>`;
  });

  const labelInterval = Math.max(Math.floor(filtered.length / 15), 1);
  filtered.forEach((s, i) => {
    if (i % labelInterval === 0) {
      svg += `<text x="${xScale(i) + groupW / 2}" y="${height - 8}" text-anchor="middle" class="chart-label">${s.strike}</text>`;
    }
  });

  [0, 0.25, 0.5, 0.75, 1].forEach((frac) => {
    const val = maxOI * frac;
    svg += `<text x="${margin.left - 8}" y="${yScale(val) + 3}" text-anchor="end" class="chart-label">${formatLargeNumber(val)}</text>`;
  });

  const spotIdx = filtered.findIndex((s) => s.strike >= spot);
  if (spotIdx >= 0) {
    const spotX = xScale(spotIdx) + groupW / 2;
    svg += `<line x1="${spotX}" y1="${margin.top}" x2="${spotX}" y2="${height - margin.bottom}" class="chart-spot-line"/>`;
    svg += `<text x="${spotX}" y="${margin.top - 5}" text-anchor="middle" class="chart-spot-label">SPOT</text>`;
  }

  svg += `<rect x="${width - 180}" y="8" width="10" height="10" fill="var(--red)" opacity="0.5" rx="1"/>`;
  svg += `<text x="${width - 166}" y="17" class="chart-label">Call OI</text>`;
  svg += `<rect x="${width - 110}" y="8" width="10" height="10" fill="var(--green)" opacity="0.5" rx="1"/>`;
  svg += `<text x="${width - 96}" y="17" class="chart-label">Put OI</text>`;

  svg += "</svg>";
  $("#oi-chart").innerHTML = svg;
}

/* ---- Avoid Section ---- */
function renderAvoid(data) {
  const avoid = data.directional.avoid;
  if (!avoid || !avoid.length) {
    $("#avoid-section").innerHTML = "";
    return;
  }

  $("#avoid-section").innerHTML = `
    <div class="avoid-list">
      ${avoid.map((a) => `<div class="avoid-item">${a}</div>`).join("")}
    </div>
  `;
}

document.addEventListener("DOMContentLoaded", init);
