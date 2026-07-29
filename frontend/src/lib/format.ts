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

  return new Intl.NumberFormat("en-US", {
    maximumFractionDigits: 2,
    minimumFractionDigits: 2,
  }).format(value);
}

const NUM_LOCALE = "en-US";

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
  if (abs >= 1e12) { scaled = value / 1e12; suffix = "T"; }
  else if (abs >= 1e9) { scaled = value / 1e9; suffix = "B"; }
  else if (abs >= 1e6) { scaled = value / 1e6; suffix = "M"; }
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

// Zerlegt ein ISO-Datum (YYYY-MM-DD) ohne Timezone-Fallen von new Date(iso).
function parseIsoDate(iso: string | null | undefined): { year: number; month: number; day: number } | null {
  if (!iso) return null;
  const m = /^(\d{4})-(\d{2})-(\d{2})$/.exec(iso.slice(0, 10));
  if (!m) return null;
  const year = Number(m[1]);
  const month = Number(m[2]);
  const day = Number(m[3]);
  // Kalender-Range pruefen — sonst rendert "2026-13-40" als 40.13. und
  // der JS-Date-Rollover verschiebt die Amber-Logik auf ein anderes Datum.
  if (month < 1 || month > 12 || day < 1 || day > 31) return null;
  return { year, month, day };
}

// Naechster Earnings-Termin fuer die Karte: "05.08." im laufenden Jahr,
// sonst mit Jahr ("05.08.2027"). null/unparsebar -> leerer String (nichts anzeigen).
export function formatEarningsDate(iso: string | null | undefined, now: Date = new Date()): string {
  const d = parseIsoDate(iso);
  if (!d) return "";
  const base = `${String(d.day).padStart(2, "0")}.${String(d.month).padStart(2, "0")}.`;
  return d.year === now.getFullYear() ? base : `${base}${d.year}`;
}

// true, wenn der Termin innerhalb der naechsten `days` Kalendertage liegt
// (heute eingeschlossen, Vergangenheit nicht) — Basis fuer die Amber-Hervorhebung.
// true, wenn der Termin in der Vergangenheit liegt (Stale-Data zwischen
// zwei Refreshes) — Karte dimmt dann und passt den Tooltip an.
export function isEarningsPast(iso: string | null | undefined, now: Date = new Date()): boolean {
  const d = parseIsoDate(iso);
  if (!d) return false;
  const target = new Date(d.year, d.month - 1, d.day).getTime();
  const today = new Date(now.getFullYear(), now.getMonth(), now.getDate()).getTime();
  return target < today;
}

export function isEarningsSoon(iso: string | null | undefined, now: Date = new Date(), days = 7): boolean {
  const d = parseIsoDate(iso);
  if (!d) return false;
  const target = new Date(d.year, d.month - 1, d.day).getTime();
  const today = new Date(now.getFullYear(), now.getMonth(), now.getDate()).getTime();
  const diffDays = Math.round((target - today) / 86_400_000);
  return diffDays >= 0 && diffDays <= days;
}

