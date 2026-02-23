const BASE = "/api";

async function fetchJSON(path) {
  const res = await fetch(`${BASE}${path}`);
  if (!res.ok) {
    const body = await res.json().catch(() => ({}));
    throw new Error(body.detail || `Request failed: ${res.status}`);
  }
  return res.json();
}

export function getTickerInfo(ticker) {
  return fetchJSON(`/ticker/${encodeURIComponent(ticker)}`);
}

export function getExpirations(ticker, minDte = 0, maxDte = 60) {
  return fetchJSON(`/expirations/${encodeURIComponent(ticker)}?min_dte=${minDte}&max_dte=${maxDte}`);
}

export function getDealerMap(ticker, expiration = null, accountSize = null) {
  let url = `/dealer-map/${encodeURIComponent(ticker)}`;
  const params = new URLSearchParams();
  if (expiration) params.set("expiration", expiration);
  if (accountSize) params.set("account_size", accountSize);
  const qs = params.toString();
  if (qs) url += `?${qs}`;
  return fetchJSON(url);
}

export function getPriceHistory(ticker, period = "3mo", interval = "1d") {
  return fetchJSON(`/price-history/${encodeURIComponent(ticker)}?period=${period}&interval=${interval}`);
}

export function getAggregateGex(ticker, maxDte = 45) {
  return fetchJSON(`/aggregate-gex/${encodeURIComponent(ticker)}?max_dte=${maxDte}`);
}
