export function formatPrice(val) {
  if (val == null || val === 0) return "—";
  return val.toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 });
}

export function formatLargeNumber(val) {
  if (val == null) return "—";
  const abs = Math.abs(val);
  if (abs >= 1e9) return `${(val / 1e9).toFixed(1)}B`;
  if (abs >= 1e6) return `${(val / 1e6).toFixed(1)}M`;
  if (abs >= 1e3) return `${(val / 1e3).toFixed(1)}K`;
  return val.toFixed(0);
}

export function formatPct(val) {
  if (val == null) return "—";
  return `${val.toFixed(2)}%`;
}

export function formatOI(val) {
  if (val == null) return "—";
  return val.toLocaleString("en-US");
}
