export function formatValue(
  rawValue: number | string | null,
  unit: string | null,
  _currency: string | null
): string {
  if (rawValue == null) return "\u2014";
  const value = typeof rawValue === "string" ? parseFloat(rawValue) : rawValue;
  if (isNaN(value)) return "\u2014";

  if (unit === "%") {
    const pct = Math.abs(value) < 1 ? value * 100 : value;
    return `${pct.toFixed(2)}%`;
  }

  if (Math.abs(value) >= 1_000_000_000) {
    return `${(value / 1_000_000_000).toFixed(2)}B`;
  }
  if (Math.abs(value) >= 1_000_000) {
    return `${(value / 1_000_000).toFixed(2)}M`;
  }

  return new Intl.NumberFormat("de-DE", {
    maximumFractionDigits: 2,
    minimumFractionDigits: value % 1 === 0 ? 0 : 2,
  }).format(value);
}
