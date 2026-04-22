/**
 * Parse a numeric input string supporting German (1.234,56) and
 * US (1,234.56) formats plus scale suffixes (Mrd/Mio/B/M/K).
 * Returns NaN for unparseable input.
 *
 * Examples:
 *   "2.558.000.000" -> 2558000000   (German thousand separators)
 *   "2,558,000,000" -> 2558000000   (US thousand separators)
 *   "2.558,50"      -> 2558.5       (German decimal)
 *   "2,558.50"      -> 2558.5       (US decimal)
 *   "2.558"         -> 2.558        (single dot = decimal)
 *   "27,65"         -> 27.65        (German decimal, no grouping)
 *   "1.45 Mrd"      -> 1450000000
 *   "14.77B"        -> 14770000000
 */
export function parseNumericInput(raw: string): number {
  if (raw == null) return NaN;
  let s = String(raw).trim().replace(/\s+/g, "");
  if (!s) return NaN;

  let multiplier = 1;
  const wordMatch = s.match(/(mrd|milliarden?|billion|mio|millionen?|tsd|tausend|thousand)\.?$/i);
  if (wordMatch) {
    const w = wordMatch[1].toLowerCase();
    if (w.startsWith("mrd") || w.startsWith("milliarde") || w === "billion") {
      multiplier = 1_000_000_000;
    } else if (w.startsWith("mio") || w.startsWith("million")) {
      multiplier = 1_000_000;
    } else {
      multiplier = 1_000;
    }
    s = s.slice(0, -wordMatch[0].length).trim();
  } else {
    const letterMatch = s.match(/([bmtk])$/i);
    if (letterMatch) {
      const L = letterMatch[1].toUpperCase();
      const map: Record<string, number> = { B: 1e9, T: 1e12, M: 1e6, K: 1e3 };
      multiplier = map[L];
      s = s.slice(0, -1).trim();
    }
  }

  s = s.replace(/%$/, "").trim();
  if (!s) return NaN;

  const hasDot = s.includes(".");
  const hasComma = s.includes(",");

  if (hasDot && hasComma) {
    const lastDot = s.lastIndexOf(".");
    const lastComma = s.lastIndexOf(",");
    if (lastComma > lastDot) {
      s = s.replace(/\./g, "").replace(",", ".");
    } else {
      s = s.replace(/,/g, "");
    }
  } else if (hasComma) {
    if (/^[+-]?\d{1,3}(,\d{3})+$/.test(s)) {
      s = s.replace(/,/g, "");
    } else {
      s = s.replace(",", ".");
    }
  } else if (hasDot) {
    if ((s.match(/\./g) ?? []).length > 1) {
      s = s.replace(/\./g, "");
    }
  }

  const n = parseFloat(s);
  if (isNaN(n) || !isFinite(n)) return NaN;
  return n * multiplier;
}
