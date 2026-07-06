export function formatValue(
  rawValue: number | string | null,
  unit: string | null,
  _currency: string | null
): string {
  if (rawValue == null) return "\u2014";
  const value = typeof rawValue === "string" ? parseFloat(rawValue) : rawValue;
  if (isNaN(value)) return "\u2014";

  if (unit === "%") {
    return `${value.toFixed(2)}%`;
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

const NUM_LOCALE = "de-DE";

export function formatNumber(value: number | null | undefined, digits = 2): string {
  if (value == null || Number.isNaN(value)) return "—";
  return new Intl.NumberFormat(NUM_LOCALE, {
    minimumFractionDigits: digits,
    maximumFractionDigits: digits,
  }).format(value);
}

export function formatShort(value: number | null | undefined, currency?: string | null): string {
  if (value == null || Number.isNaN(value)) return "—";
  const abs = Math.abs(value);
  let scaled: number;
  let suffix: string;
  if (abs >= 1e12) { scaled = value / 1e12; suffix = "Bio"; }
  else if (abs >= 1e9) { scaled = value / 1e9; suffix = "Mrd"; }
  else if (abs >= 1e6) { scaled = value / 1e6; suffix = "Mio"; }
  else { scaled = value; suffix = ""; }
  const digits = Math.abs(scaled) >= 100 ? 0 : Math.abs(scaled) >= 10 ? 1 : 2;
  const num = formatNumber(scaled, digits);
  return currency ? `${num}${suffix ? " " + suffix : ""} ${currency}` : `${num}${suffix ? " " + suffix : ""}`;
}

export function formatPercent(value: number | null | undefined, digits = 2): string {
  if (value == null || Number.isNaN(value)) return "—";
  return `${formatNumber(value, digits)} %`;
}

export function formatRelative(iso: string | null | undefined): string {
  if (!iso) return "—";
  const then = new Date(iso).getTime();
  if (Number.isNaN(then)) return "—";
  const sec = Math.round((Date.now() - then) / 1000);
  if (sec < 60) return "just now";
  const min = Math.round(sec / 60);
  if (min < 60) return `${min} min ago`;
  const hr = Math.round(min / 60);
  if (hr < 24) return `${hr} h ago`;
  const day = Math.round(hr / 24);
  if (day < 30) return `${day} ${day === 1 ? "day" : "days"} ago`;
  const mon = Math.round(day / 30);
  if (mon < 12) return `${mon} ${mon === 1 ? "month" : "months"} ago`;
  const yr = Math.round(mon / 12);
  return `${yr} ${yr === 1 ? "year" : "years"} ago`;
}

export function formatAbsolute(iso: string | null | undefined): string {
  if (!iso) return "—";
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return "—";
  return d.toLocaleString("en-GB", {
    day: "2-digit", month: "short", year: "numeric",
    hour: "2-digit", minute: "2-digit",
  });
}

export function formatFyEnd(month: number | null, day: number | null): string {
  if (!month || !day) return "auto";
  return `${String(day).padStart(2, "0")}.${String(month).padStart(2, "0")}.`;
}

