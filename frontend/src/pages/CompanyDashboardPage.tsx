import { useEffect, useState, useCallback } from "react";
import { Link, useParams } from "react-router-dom";
import { ChevronLeft, ChevronRight, ChevronDown, RefreshCw, Info, X, Plus, ShieldCheck, Calculator, MessageSquare, Pencil, Sparkles, AlertTriangle, Loader2, Lock } from "lucide-react";
import { toast } from "sonner";
import { createPortal } from "react-dom";
import { AppHeader } from "@/components/AppHeader";
import { useAuth } from "@/hooks/useAuth";
import { t } from "@/lib/i18n";
import { formatValue } from "@/lib/format";
import { listCompanies, type Company } from "@/api/companies";
import {
  getValueDefinitions,
  getCompanyValues,
  getCumulativeValues,
  getFyAvailability,
  getRefreshStatus,
  refreshValues,
  overrideValue,
  explainValue,
  fetchHistoricalStammdaten,
  type ValueDefinition,
  type CompanyValue,
  type CumulativeValuesResponse,
  type FyAvailability,
  type RefreshStatus,
} from "@/api/values";
import { CumulativeBreakdownDrawer } from "@/components/CumulativeBreakdownDrawer";
import { RefreshProgressBar } from "@/components/RefreshProgressBar";
import { getFxRates } from "@/api/fx";
import { parseNumericInput } from "@/lib/parseNumeric";

const CATEGORY_ORDER = [
  "HOHN_RETURN", "VALUATION", "FCF", "NI_GROWTH", "SBC", "BUYBACKS",
  "DIVIDENDS", "DEBT", "STAMMDATEN",
];

// Faktoren die in der Hohn-Formel auftauchen — die zeigen wir in der
// kompakten Ansicht. buyback_yield (brutto) ist NICHT in der Formel,
// nur net_buyback_yield (= Buybacks − SBC) — daher hier raus.
const FACTOR_KEYS = new Set([
  "hohn_return_simple",
  "hohn_return_detailed",
  "h_peg",
  "actual_return",
  "fcf_yield",
  "ni_growth",
  "sbc_yield",
  "net_buyback_yield",
  "dividend_yield",
  "net_debt_change_pct",
  "market_cap",
]);

const PREV_YEAR_DISPLAY_KEYS = new Set([
  "net_income",
  "net_debt",
]);

const CUM_INPUT_FY_KEYS = [
  "fcf",
  "net_income",
  "sbc",
  "buyback_volume",
  "dividends",
  "net_debt",
];


const CATEGORY_LABELS: Record<string, string> = {
  HOHN_RETURN: "H-Return",
  VALUATION: "Bewertung",
  FCF: "Free Cash Flow",
  NI_GROWTH: "Net Income Growth",
  SBC: "SBC",
  BUYBACKS: "Buybacks",
  DIVIDENDS: "Dividend Yield",
  DEBT: "Net Debt",
  STAMMDATEN: "Stammdaten",
};

const CATEGORY_COLORS: Record<string, string> = {
  STAMMDATEN: "bg-slate-100 text-slate-700 border-slate-200",
  DEBT: "bg-rose-50 text-rose-700 border-rose-200",
  SBC: "bg-amber-50 text-amber-700 border-amber-200",
  BUYBACKS: "bg-orange-50 text-orange-700 border-orange-200",
  FCF: "bg-teal-50 text-teal-700 border-teal-200",
  NI_GROWTH: "bg-violet-50 text-violet-700 border-violet-200",
  DIVIDENDS: "bg-emerald-50 text-emerald-700 border-emerald-200",
  HOHN_RETURN: "bg-sky-100 text-sky-800 border-sky-300",
  VALUATION: "bg-indigo-100 text-indigo-800 border-indigo-300",
};

type PeriodOption =
  | { label: string; value: "FY"; year: number; from_year?: undefined; to_year?: undefined }
  | { label: string; value: "CUM"; year?: undefined; from_year: number; to_year: number };

const FY_OPTIONS: { label: string; year: number }[] = [
  { label: "FY 2026e", year: 2026 },
  { label: "FY 2025", year: 2025 },
  { label: "FY 2024", year: 2024 },
  { label: "FY 2023", year: 2023 },
  { label: "FY 2022", year: 2022 },
  { label: "FY 2021", year: 2021 },
  { label: "FY 2020", year: 2020 },
];

const CUM_TO_YEAR = 2025;
const CUM_FROM_YEARS = [2024, 2023, 2022, 2021, 2020];
const CUM_OPTIONS: { label: string; from_year: number; to_year: number }[] = CUM_FROM_YEARS.map((fy) => ({
  label: `seit ${fy}`,
  from_year: fy,
  to_year: CUM_TO_YEAR,
}));

const FALLBACK_FX_RATES: Record<string, number> = {
  USD: 1, EUR: 0.92, GBP: 0.79, CHF: 0.88, JPY: 155, KRW: 1390,
  HKD: 7.8, CNY: 7.2, CAD: 1.35, AUD: 1.52, SEK: 10.5, NOK: 10.5,
  DKK: 6.9, SGD: 1.34, INR: 83, BRL: 5.0, MXN: 17, ZAR: 18.5,
};
const CURRENCIES = ["USD", "EUR", "GBP", "CHF", "JPY", "KRW", "CNY", "HKD"];

const FORMULAS: Record<string, string> = {
  market_cap_calc: "Stock Price × Shares Outstanding",
  net_buyback: "Buyback-Volumen − SBC",
  sbc_yield: "SBC / Market Cap × 100",
  buyback_yield: "Buyback Volume / Market Cap × 100",
  net_buyback_yield: "Net Buyback / Market Cap × 100",
  fcf_yield: "FCF / Market Cap × 100",
  ni_growth: "(NI[Y] − NI[Y−1]) / |NI[Y−1]| × 100",
  net_debt_change: "Net Debt[Y−1] − Net Debt[Y]",
  net_debt_change_pct: "ΔNet Debt / Market Cap × 100",
  dividend_yield: "Dividends / Market Cap × 100",
  hohn_return_simple: "FCF Yield + NI Growth − SBC/MCap + ΔND/MCap",
  hohn_return_detailed: "Dividend Yield + NI Growth + Net Buyback/MCap + ΔND/MCap",
  actual_return: "(MCap Ende FY / MCap Anfang FY − 1) × 100  [TSR via Yahoo Adj Close]",
  pe_ratio: "Market Cap / Net Income  (KGV, nur bei positivem NI)",
  ev_ebitda: "(Market Cap + Net Debt) / EBITDA",
};

type ColorTier = "excellent" | "good" | "neutral" | "weak" | "bad";

const HIGHER_BETTER_THRESHOLDS: Record<string, [number, number, number, number]> = {
  hohn_return_simple: [15, 10, 5, 0],
  hohn_return_detailed: [15, 10, 5, 0],
  fcf_yield: [6, 4, 2, 0],
  ni_growth: [15, 8, 0, -5],
  buyback_yield: [4, 2, 0.5, 0],
  net_buyback_yield: [3, 1.5, 0, -1],
  dividend_yield: [3, 1.5, 0.5, 0],
  net_debt_change_pct: [3, 0.5, -1, -3],
};

const LOWER_BETTER_THRESHOLDS: Record<string, [number, number, number, number]> = {
  sbc_yield: [0.5, 1, 2, 4],
};

function colorTier(key: string, value: number | null): ColorTier | null {
  if (value == null || isNaN(value)) return null;
  const hb = HIGHER_BETTER_THRESHOLDS[key];
  if (hb) {
    const [exc, good, weak, bad] = hb;
    if (value >= exc) return "excellent";
    if (value >= good) return "good";
    if (value >= weak) return "neutral";
    if (value >= bad) return "weak";
    return "bad";
  }
  const lb = LOWER_BETTER_THRESHOLDS[key];
  if (lb) {
    const [exc, good, weak, bad] = lb;
    if (value <= exc) return "excellent";
    if (value <= good) return "good";
    if (value <= weak) return "neutral";
    if (value <= bad) return "weak";
    return "bad";
  }
  return null;
}

const TIER_BG: Record<ColorTier, string> = {
  excellent: "bg-emerald-100/70",
  good: "bg-green-50",
  neutral: "",
  weak: "bg-orange-50",
  bad: "bg-red-100/60",
};

const HOHN_LOCKED_KEYS = new Set(["hohn_return_simple", "hohn_return_detailed"]);

// Primäre Werte die im Estimate-Mode per Q-Faktor geschätzt werden.
// Nur für diese ist die Q-Faktor-Cell anklickbar (zeigt Drilldown:
// FY[N-1]-Wert + Faktor-Berechnung).
const ESTIMATE_PRIMARY_KEYS = new Set([
  "net_income", "fcf", "sbc", "buyback_volume", "dividends",
  "net_debt", "shares_outstanding", "ebitda",
]);

type VariantValues = Map<string, number | null>;


// CALCULATED Trailing-Werte (pe_ratio, ev_ebitda, fcf_yield) im Forecast-Year
// werden methoden-unabhaengig berechnet (FY[N-1]-Anker). Sie sollen in BEIDEN
// Estimate-Rows (Q-Faktor + Web) den gleichen Trailing-Wert zeigen.
const VALUATION_TRAILING_KEYS = new Set(["pe_ratio", "ev_ebitda", "fcf_yield"]);

// Keys mit Adjusted/Non-GAAP-Variante. Andere Keys (SBC, Buyback, Dividends,
// Net Debt, Shares Outstanding) sind per Definition Reported-only.
// Inputs + Calculated-Multiples deren Adjusted-Variante backend-seitig
// persistiert wird (numeric_value_adjusted). Im Estimate-Mode (Forecast-Year)
// muessen die Calc-Multiples hier listed sein, damit der Toggle den
// backend-persistierten Adjusted-Wert direkt zieht (kein Frontend-Recompute).
const ADJUSTABLE_INPUT_KEYS = new Set([
  "net_income", "ebitda", "fcf",
  "pe_ratio", "ev_ebitda", "fcf_yield", "ni_growth",
  "hohn_return_simple", "hohn_return_detailed", "h_peg",
]);

type ValuationMode = "reported" | "adjusted";

const VALUATION_MODE_STORAGE_KEY = "hohn:valuation_mode";

function loadValuationMode(): ValuationMode {
  try {
    const stored = localStorage.getItem(VALUATION_MODE_STORAGE_KEY);
    return stored === "adjusted" ? "adjusted" : "reported";
  } catch { return "reported"; }
}

function saveValuationMode(mode: ValuationMode): void {
  try { localStorage.setItem(VALUATION_MODE_STORAGE_KEY, mode); } catch { /* ignore */ }
}

// Mode-aware Wert: gibt im Adjusted-Mode den Adjusted-Wert zurueck wenn
// vorhanden — sonst Fallback auf Reported mit Marker-Flag fuer UI.
type ModeValue = {
  value: number | null;
  isAdjustedActive: boolean;     // true wenn echter Adj-Wert verwendet wurde
  isFallbackToReported: boolean; // true wenn Adj-Mode aber Adj-Wert null -> Reported genutzt
};

function _toNumSafe(n: number | string | null | undefined): number | null {
  if (n == null) return null;
  return typeof n === "string" ? parseFloat(n) : n;
}

function getModeValue(cv: CompanyValue | null | undefined, mode: ValuationMode): ModeValue {
  if (!cv) return { value: null, isAdjustedActive: false, isFallbackToReported: false };
  const reported = _toNumSafe(cv.numeric_value);
  const adjusted = _toNumSafe(cv.numeric_value_adjusted ?? null);
  if (mode === "reported") {
    return { value: reported, isAdjustedActive: false, isFallbackToReported: false };
  }
  // Adjusted-Mode: nur wenn Key adjusted-relevant ist und Adjusted-Wert da
  if (ADJUSTABLE_INPUT_KEYS.has(cv.value_key) && adjusted != null) {
    return { value: adjusted, isAdjustedActive: true, isFallbackToReported: false };
  }
  // Fallback: Reported anzeigen wenn Adjusted-Mode aktiv aber kein Adj-Wert
  // — gilt sowohl fuer adjustable Keys ohne reportet Adj als auch fuer
  // non-adjustable Keys (SBC, etc.) die per Definition gleich sind.
  const isFallback = ADJUSTABLE_INPUT_KEYS.has(cv.value_key);
  return { value: reported, isAdjustedActive: false, isFallbackToReported: isFallback };
}

function _isTrailingValuation(cv: CompanyValue): boolean {
  return VALUATION_TRAILING_KEYS.has(cv.value_key)
    && !!cv.is_forecast
    && (cv.source_name || "").includes("Bewertung Stand FY");
}

// Helper: gibt den "primary" Wert eines cv zurueck — entweder Reported
// (numeric_value) oder Adjusted (numeric_value_adjusted) je nach Mode.
// Fallback auf Reported wenn Adjusted nicht da.
function _primaryByMode(cv: CompanyValue, mode: ValuationMode): number | null {
  const mv = getModeValue(cv, mode);
  return mv.value;
}

function _getFaktorValue(cv: CompanyValue, mode: ValuationMode = "reported"): number | null {
  if (!cv.is_forecast) return _primaryByMode(cv, mode);
  // Trailing-Bewertung: methoden-unabhaengig, immer den Wert zeigen.
  if (_isTrailingValuation(cv)) return _primaryByMode(cv, mode);
  // primary_method ist explizit (neu) — Source-Name nur als Legacy-Fallback
  // fuer alte Rows ohne primary_method.
  const isProxyPrimary = cv.primary_method === "q_factor_proxy"
    || (cv.primary_method == null && (cv.source_name || "").includes("Proxy"));
  if (isProxyPrimary && !cv.manually_overridden) return _primaryByMode(cv, mode);
  const alt = cv.forecast_alternates?.find((a) => a.method === "q_factor_proxy");
  if (alt?.value != null) return parseFloat(alt.value);
  if (cv.manually_overridden) return _primaryByMode(cv, mode);
  return null;
}

function _getWebValue(cv: CompanyValue, mode: ValuationMode = "reported"): number | null {
  if (!cv.is_forecast) return _primaryByMode(cv, mode);
  // Trailing-Bewertung: methoden-unabhaengig, immer den Wert zeigen.
  if (_isTrailingValuation(cv)) return _primaryByMode(cv, mode);
  const isWebPrimary = cv.primary_method === "web_guidance"
    || (cv.primary_method == null && (cv.source_name || "").includes("Web-Guidance"));
  if (isWebPrimary && !cv.manually_overridden) return _primaryByMode(cv, mode);
  const alt = cv.forecast_alternates?.find((a) => a.method === "web_guidance");
  if (alt?.value != null) return parseFloat(alt.value);
  if (cv.manually_overridden) return _primaryByMode(cv, mode);
  return null;
}

function _safeYield(v: number | null, mcap: number | null): number | null {
  if (v == null || mcap == null || mcap === 0) return null;
  return (v / mcap) * 100;
}

function buildVariantValues(
  rows: CompanyValue[],
  prevRows: CompanyValue[],
  variant: "faktor" | "web" | "fy",
  mode: ValuationMode = "reported",
): VariantValues {
  const pickFn = variant === "faktor"
    ? _getFaktorValue
    : variant === "web"
      ? _getWebValue
      : (cv: CompanyValue, m: ValuationMode) => _primaryByMode(cv, m);
  const raw = new Map<string, number | null>();
  for (const r of rows) raw.set(r.value_key, pickFn(r, mode));
  // Prev-Werte mode-konsistent: bei Mode=Adjusted nutzt NI-Growth Adj-NI
  // im Zähler UND Nenner (sonst Apples-to-Oranges-Vergleich).
  const prev = new Map<string, number | null>();
  for (const r of prevRows) prev.set(r.value_key, _primaryByMode(r, mode));

  const fcf = raw.get("fcf") ?? null;
  const sbc = raw.get("sbc") ?? null;
  const buyback = raw.get("buyback_volume") ?? null;
  const dividends = raw.get("dividends") ?? null;
  const netIncome = raw.get("net_income") ?? null;
  const netDebt = raw.get("net_debt") ?? null;
  const marketCap = raw.get("market_cap") ?? null;
  const niPrev = prev.get("net_income") ?? null;
  const ndPrev = prev.get("net_debt") ?? null;

  const fcfYield = _safeYield(fcf, marketCap);
  const sbcYield = _safeYield(sbc, marketCap);
  const divYield = _safeYield(dividends, marketCap);
  const buybackYield = _safeYield(buyback, marketCap);
  const netBuyback = (buyback != null && sbc != null) ? buyback - sbc : null;
  const netBuybackYield = _safeYield(netBuyback, marketCap);

  let niGrowth: number | null = null;
  if (netIncome != null && niPrev != null && niPrev !== 0) {
    niGrowth = ((netIncome - niPrev) / Math.abs(niPrev)) * 100;
  }
  let ndChange: number | null = null;
  let ndChangePct: number | null = null;
  if (netDebt != null && ndPrev != null) {
    ndChange = ndPrev - netDebt;
    ndChangePct = _safeYield(ndChange, marketCap);
  }

  const sumIfAllPresent = (parts: (number | null)[]): number | null => {
    if (parts.some((p) => p == null)) return null;
    return (parts as number[]).reduce((a, b) => a + b, 0);
  };
  const hohnSimple = sumIfAllPresent([fcfYield, niGrowth, sbcYield != null ? -sbcYield : null, ndChangePct]);
  const hohnDetailed = sumIfAllPresent([divYield, niGrowth, netBuybackYield, ndChangePct]);

  // VALUATION-Multiples mode-aware rechnen aus den mode-spezifischen Inputs.
  // Reported-Mode: nutzt Reported NI/EBITDA. Adjusted-Mode: Adjusted-Werte
  // (siehe pick-Logic oben). So bekommt pe_ratio/ev_ebitda automatisch die
  // 'Adjusted-Variante' im Adjusted-Mode ohne Backend-Persistenz noetig.
  const ebitdaVal = raw.get("ebitda") ?? null;
  let peRatio: number | null = null;
  if (marketCap != null && netIncome != null && netIncome > 0) {
    peRatio = marketCap / netIncome;
  }
  let evEbitda: number | null = null;
  if (marketCap != null && ebitdaVal != null && ebitdaVal > 0) {
    const ev = marketCap + (netDebt ?? 0);
    evEbitda = ev / ebitdaVal;
  }
  return new Map<string, number | null>([
    ["fcf", fcf], ["sbc", sbc], ["buyback_volume", buyback], ["dividends", dividends],
    ["net_income", netIncome], ["net_debt", netDebt],
    ["market_cap", marketCap], ["shares_outstanding", raw.get("shares_outstanding") ?? null],
    ["stock_price", raw.get("stock_price") ?? null], ["market_cap_calc", raw.get("market_cap_calc") ?? null],
    ["fcf_yield", fcfYield], ["sbc_yield", sbcYield], ["dividend_yield", divYield],
    ["buyback_yield", buybackYield], ["net_buyback", netBuyback], ["net_buyback_yield", netBuybackYield],
    ["ni_growth", niGrowth], ["net_debt_change", ndChange], ["net_debt_change_pct", ndChangePct],
    ["hohn_return_simple", hohnSimple], ["hohn_return_detailed", hohnDetailed],
    ["pe_ratio", peRatio],
    ["ev_ebitda", evEbitda],
    ["ebitda", ebitdaVal],
    ["actual_return", raw.get("actual_return") ?? null],
  ]);
}


type EstimateLockReason = "missing_target_q_reports" | "missing_q_reports" | "missing_prev_fy_data" | null;

function getEstimateLockReason(
  av: FyAvailability | undefined,
  periodYear: number | undefined,
): EstimateLockReason {
  // Estimate-Mode benoetigt FY[N-1]-Daten als Anker (NI-Growth, Net-Debt-Change
  // brauchen Vorjahres-Werte). Ohne Vorjahres-AR ist die Schaetzung blind.
  // Q-Faktor wurde entfernt — keine Q-Report-Pflicht mehr.
  if (!av || periodYear === undefined) return null;
  if (av.is_us) return null;
  const prevYear = periodYear - 1;
  if (!av.fy_years_with_data.includes(prevYear)) return "missing_prev_fy_data";
  return null;
}

function isEstimateLocked(av: FyAvailability | undefined, periodYear: number | undefined): boolean {
  return getEstimateLockReason(av, periodYear) === "missing_prev_fy_data";
}

function _escapeHtml(s: string): string {
  return s.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/"/g, "&quot;");
}

type QuarterBreakdown = {
  q: 1 | 2 | 3 | 4;
  value: number;
  isActual: boolean;
  context: string;
};

type ParsedBreakdown = {
  generalSource: string;
  quarters: QuarterBreakdown[];
  fyTotal: { value: number; raw: string } | null;
};

function _parseNumberWithUnit(raw: string, unit: string | undefined): number {
  let valueStr = raw.trim();
  // Locale: "7,27" → 7.27; "7.27" → unchanged; "1,234.56" or "1.234,56" → last separator is decimal
  if (valueStr.includes(",") && !valueStr.includes(".")) {
    valueStr = valueStr.replace(",", ".");
  } else if (valueStr.includes(",") && valueStr.includes(".")) {
    const lastComma = valueStr.lastIndexOf(",");
    const lastDot = valueStr.lastIndexOf(".");
    if (lastComma > lastDot) valueStr = valueStr.replace(/\./g, "").replace(",", ".");
    else valueStr = valueStr.replace(/,/g, "");
  }
  let value = parseFloat(valueStr);
  if (isNaN(value)) return NaN;
  const u = (unit || "").toLowerCase();
  if (u.startsWith("mrd") || u === "b" || u.startsWith("milliarde") || u.startsWith("billion")) value *= 1e9;
  else if (u.startsWith("mio") || u === "m" || u.startsWith("millione") || u.startsWith("million")) value *= 1e6;
  return value;
}

/**
 * Extrahiert die Quartals-Aufschluesselung + FY-Total aus einem LLM-Source-String.
 * Erwartetes Format (von der Konsens-Anchoring-Pflicht im Backend-Prompt):
 *   "... | Quartals-Aufschluesselung: Q1 $8.0B (actual lt. 10-Q),
 *    Q2 $10.3B (actual), Q3e ~$15.5B (Konsens), Q4e $17.5B (Konsens)
 *    = FY-Total $51.3B"
 *
 * Parst nur den ERSTEN gefundenen Block (Claude's Antwort — Gemini's
 * Block kommt danach und ist redundant). Returns null wenn kein Block gefunden.
 */
function parseQuartalsBreakdown(source: string | null | undefined): ParsedBreakdown | null {
  if (!source) return null;
  // Akzeptiert beide Schreibweisen:
  //   "Quartals-Aufschlüsselung" (Umlaut: ü + sselung)
  //   "Quartals-Aufschluesselung" (ASCII: ue + sselung)
  const markerRegex = /Quartals[- ]?Auf?schl(?:ü|ue)s+elung\s*:/i;
  const markerMatch = source.match(markerRegex);
  if (!markerMatch) return null;
  const markerIdx = markerMatch.index ?? -1;
  if (markerIdx < 0) return null;

  const generalSource = source.slice(0, markerIdx).replace(/\s*\|\s*$/, "").trim();
  const breakdownPart = source.slice(markerIdx + markerMatch[0].length);

  const quarters: QuarterBreakdown[] = [];
  const qRegex = /Q([1-4])(e)?\s*[~≈]?\s*[\$€£]?\s*([\d.,]+)\s*(B|Mrd|Mio|M|Milliarden|Millionen|billion|million)?\b/gi;
  let m: RegExpExecArray | null;
  const seenQ = new Set<number>();
  while ((m = qRegex.exec(breakdownPart)) !== null) {
    const q = parseInt(m[1]) as 1 | 2 | 3 | 4;
    if (seenQ.has(q)) continue;
    const hasE = m[2] === "e";
    const value = _parseNumberWithUnit(m[3], m[4]);
    if (isNaN(value)) continue;
    const after = breakdownPart.slice(m.index, m.index + 250);
    const ctxMatch = after.match(/\(([^)]*)\)/);
    const context = ctxMatch ? ctxMatch[1] : "";
    const isActual = !hasE && /\bactual\b/i.test(context);
    quarters.push({ q, value, isActual, context });
    seenQ.add(q);
  }
  quarters.sort((a, b) => a.q - b.q);

  let fyTotal: { value: number; raw: string } | null = null;
  const fyRegex = /FY[- ]?(?:Total|Summe)\s*[~≈]?\s*[\$€£]?\s*([\d.,]+)\s*(B|Mrd|Mio|M|Milliarden|Millionen|billion|million)?\b/i;
  const fyMatch = breakdownPart.match(fyRegex);
  if (fyMatch) {
    const fyValue = _parseNumberWithUnit(fyMatch[1], fyMatch[2]);
    if (!isNaN(fyValue)) fyTotal = { value: fyValue, raw: fyMatch[0] };
  }

  if (quarters.length === 0 && !fyTotal) return null;
  return { generalSource, quarters, fyTotal };
}

function _formatShortMoney(value: number, currency: string | null = "USD"): string {
  const sign = value < 0 ? "-" : "";
  const abs = Math.abs(value);
  const sym = currency === "EUR" ? "€" : currency === "GBP" ? "£" : "$";
  if (abs >= 1e9) return `${sign}${sym}${(abs / 1e9).toFixed(2)} B`;
  if (abs >= 1e6) return `${sign}${sym}${(abs / 1e6).toFixed(0)} M`;
  return `${sign}${sym}${abs.toFixed(0)}`;
}

function renderMarkdown(text: string): string {
  // Light Markdown: **bold**, *italic*, headings ##, bullets - / *, line breaks,
  // code `inline`. Robust gegen XSS via _escapeHtml.
  const escaped = _escapeHtml(text);
  return escaped
    // Headings
    .replace(/^###\s+(.+)$/gm, '<h4 class="mt-2 mb-1 text-[12px] font-bold text-foreground">$1</h4>')
    .replace(/^##\s+(.+)$/gm, '<h3 class="mt-2 mb-1 text-[13px] font-bold text-foreground">$1</h3>')
    // Bold + Italic
    .replace(/\*\*(.+?)\*\*/g, '<strong class="font-semibold text-foreground">$1</strong>')
    .replace(/(?<!\*)\*([^*\n]+)\*(?!\*)/g, '<em class="italic">$1</em>')
    // Inline code
    .replace(/`([^`\n]+)`/g, '<code class="rounded bg-muted px-1 font-mono text-[10px]">$1</code>')
    // Links [label](url)
    .replace(/\[([^\]]+)\]\((https?:\/\/[^\s)]+)\)/g, '<a href="$2" target="_blank" rel="noreferrer" class="text-primary underline hover:opacity-80">$1</a>')
    // Bullets (- foo  oder  * foo)
    .replace(/^[ \t]*[-*]\s+(.+)$/gm, '<li class="ml-4 list-disc">$1</li>')
    // Wrap consecutive <li> in <ul>
    .replace(/(<li[^>]*>.*<\/li>\n?)+/g, (m) => `<ul class="my-1 space-y-0.5">${m.replace(/\n/g, "")}</ul>`)
    // Numbered lists "1. foo"
    .replace(/^[ \t]*(\d+)\.\s+(.+)$/gm, '<div class="ml-4"><span class="font-medium text-muted-foreground">$1.</span> $2</div>')
    // Line breaks (alle die uebrig sind)
    .replace(/\n/g, "<br />");
}

function isFyHistoricalLocked(av: FyAvailability | undefined, periodYear: number | undefined): boolean {
  // Abgeschlossenes FY (period_year < currentYear) + Non-US + kein Annual Report
  // hochgeladen → ganze Firma-Zeile gelockt (analog zu Estimate-Lock).
  // US-Filer: nie gelockt (Yahoo/EDGAR liefert Daten).
  // Laufendes FY (>= currentYear): wird durch Estimate-Lock-Logik abgedeckt.
  if (!av || periodYear === undefined) return false;
  if (periodYear >= new Date().getFullYear()) return false;
  if (av.is_us) return false;
  return !av.annual_report_years.includes(periodYear);
}

function isHohnLocked(av: FyAvailability | undefined, periodYear: number | undefined): boolean {
  if (!av || periodYear === undefined) return false;
  if (periodYear >= new Date().getFullYear()) return false;
  if (av.is_us) return false;
  return !av.annual_report_years.includes(periodYear);
}

type TooltipState = {
  key: string;
  companyId: string;
  x: number;
  y: number;
  variant?: "faktor" | "web";
} | null;

export function CompanyDashboardPage() {
  const { pid } = useParams<{ pid: string }>();
  const { user, logout } = useAuth();

  const [definitions, setDefinitions] = useState<ValueDefinition[]>([]);
  const [companies, setCompanies] = useState<Company[]>([]);
  const [valuesMap, setValuesMap] = useState<Map<string, CompanyValue[]>>(new Map());
  const [periodMode, setPeriodMode] = useState<"FY" | "CUM">("FY");
  const [fyIdx, setFyIdx] = useState(0);
  const [cumIdx, setCumIdx] = useState(0);
  const [displayCurrency, setDisplayCurrency] = useState("USD");
  const [isLoadingPeriod, setIsLoadingPeriod] = useState(false);
  const [valuationMode, setValuationMode] = useState<ValuationMode>(() => loadValuationMode());
  const toggleValuationMode = (m: ValuationMode) => {
    setValuationMode(m);
    saveValuationMode(m);
  };
  const [expandedSections, setExpandedSections] = useState<Set<string>>(new Set());
  const [tooltip, setTooltip] = useState<TooltipState>(null);
  const [explainResult, setExplainResult] = useState<{ key: string; companyId: string; periodYear: number | null; text: string } | null>(null);
  const [explainLoading, setExplainLoading] = useState(false);
  const [tooltipEdit, setTooltipEdit] = useState<{ key: string; companyId: string; periodYear: number | null; value: string } | null>(null);
  const [tooltipEditSaving, setTooltipEditSaving] = useState(false);
  const [editCell, setEditCell] = useState<{ companyId: string; key: string; value: string } | null>(null);
  const [saving, setSaving] = useState(false);
  const [notFound, setNotFound] = useState<Set<string>>(new Set());
  const [cumDrawer, setCumDrawer] = useState<{ companyId: string; companyName: string; valueKey: string; valueLabel: string } | null>(null);
  const [cumDrawerOpen, setCumDrawerOpen] = useState(false);
  const [prevYearValuesMap, setPrevYearValuesMap] = useState<Map<string, CompanyValue[]>>(new Map());
  const [cumulativeMap, setCumulativeMap] = useState<Map<string, CumulativeValuesResponse>>(new Map());
  const [availabilityMap, setAvailabilityMap] = useState<Map<string, FyAvailability>>(new Map());
  const [refreshStatuses, setRefreshStatuses] = useState<Map<string, RefreshStatus>>(new Map());
  const [fxRates, setFxRates] = useState<Record<string, number>>(FALLBACK_FX_RATES);

  const period: PeriodOption = periodMode === "FY"
    ? { label: FY_OPTIONS[fyIdx].label, value: "FY", year: FY_OPTIONS[fyIdx].year }
    : { label: CUM_OPTIONS[cumIdx].label, value: "CUM", from_year: CUM_OPTIONS[cumIdx].from_year, to_year: CUM_OPTIONS[cumIdx].to_year };

  const loadAllValues = useCallback(async () => {
    if (!pid || companies.length === 0) return;
    setIsLoadingPeriod(true);
    try {
    const availMap = new Map<string, FyAvailability>();
    await Promise.all(
      companies.map(async (c) => {
        try {
          availMap.set(c.id, await getFyAvailability(c.id));
        } catch {
          availMap.set(c.id, { fy_years_with_data: [], keys_per_year: {}, has_snapshot_market_cap: false, is_us: false, annual_report_years: [], quarter_years: [], quarter_years_in_progress: [] });
        }
      })
    );
    setAvailabilityMap(availMap);

    const qualitativeOnlyKeys = new Set(
      definitions.filter((d) => d.source_type === "QUALITATIVE").map((d) => d.key)
    );
    const alwaysCurrentApiKeys = new Set(
      definitions.filter((d) => d.always_current && d.source_type !== "QUALITATIVE").map((d) => d.key)
    );
    // STAMMDATEN-Keys (stock_price, market_cap, shares_outstanding, market_cap_calc)
    // im laufenden/zukuenftigen FY: SNAPSHOT (heute) bevorzugen statt 01.01.-FY-Anker.
    // Kunden-Anforderung: bei Estimates sieht der User Werte von HEUTE, nicht 01.01.
    const stammdatenKeys = new Set(
      definitions.filter((d) => d.category === "STAMMDATEN").map((d) => d.key)
    );
    const useSnapshotForStammdaten = period.value === "FY"
      && period.year !== undefined
      && period.year >= new Date().getFullYear();

    if (period.value === "CUM") {
      const cumMap = new Map<string, CumulativeValuesResponse>();
      const snapMap = new Map<string, CompanyValue[]>();
      await Promise.all(
        companies.map(async (c) => {
          try {
            const cum = await getCumulativeValues(c.id, period.from_year, period.to_year);
            cumMap.set(c.id, cum);
          } catch {
            // skip
          }
          try {
            const snap = await getCompanyValues(c.id, "SNAPSHOT");
            snapMap.set(c.id, snap);
          } catch {
            // skip
          }
        })
      );
      setCumulativeMap(cumMap);
      setValuesMap(snapMap);
      setPrevYearValuesMap(new Map());
      return;
    }

    const map = new Map<string, CompanyValue[]>();
    const prevMap = new Map<string, CompanyValue[]>();
    const wantsPrev = period.value === "FY" && period.year !== undefined && period.year > 0;
    const isHistoricalFy = period.value === "FY" && period.year !== undefined && period.year < new Date().getFullYear();
    await Promise.all(
      companies.map(async (c) => {
        if (isHistoricalFy && period.year !== undefined) {
          try { await fetchHistoricalStammdaten(c.id, period.year); } catch {}
          if (!c.fiscal_year_end_month || !c.fiscal_year_end_day) {
            // FY-end may have been auto-detected on backend → refresh company state
            try {
              const fresh = await listCompanies(pid);
              setCompanies(fresh);
            } catch {}
          }
        }
        const periodVals = await getCompanyValues(c.id, period.value, period.year);
        const snapshotVals = await getCompanyValues(c.id, "SNAPSHOT");
        const periodKeyMap = new Map(periodVals.map((v) => [v.value_key, v]));
        const allKeys = new Set([...periodVals.map((v) => v.value_key), ...snapshotVals.map((v) => v.value_key)]);
        const merged = [...allKeys].map((key) => {
          if (qualitativeOnlyKeys.has(key)) {
            return snapshotVals.find((v) => v.value_key === key) ?? periodKeyMap.get(key);
          }
          // Estimate-Mode (laufendes/zukuenftiges FY): Stammdaten aus SNAPSHOT
          // (heute), nicht 01.01.-FY-Anker.
          if (useSnapshotForStammdaten && stammdatenKeys.has(key)) {
            return snapshotVals.find((v) => v.value_key === key) ?? periodKeyMap.get(key);
          }
          if (alwaysCurrentApiKeys.has(key)) {
            return periodKeyMap.get(key) ?? snapshotVals.find((v) => v.value_key === key);
          }
          return periodKeyMap.get(key) ?? snapshotVals.find((v) => v.value_key === key);
        }).filter(Boolean) as CompanyValue[];
        map.set(c.id, merged);
        if (wantsPrev) {
          try {
            const prevVals = await getCompanyValues(c.id, "FY", (period.year as number) - 1);
            prevMap.set(c.id, prevVals);
          } catch {
            prevMap.set(c.id, []);
          }
        }
      })
    );
    setValuesMap(map);
    setPrevYearValuesMap(prevMap);
    } finally {
      setIsLoadingPeriod(false);
    }
  }, [pid, companies, period.value, period.year, period.from_year, period.to_year, definitions]);

  const pollStatuses = useCallback(async (companyList: Company[]) => {
    if (companyList.length === 0) return;
    const entries = await Promise.all(
      companyList.map(async (c) => {
        try {
          const s = await getRefreshStatus(c.id);
          return [c.id, s] as [string, RefreshStatus];
        } catch {
          return [c.id, { status: "idle" } as RefreshStatus] as [string, RefreshStatus];
        }
      })
    );
    setRefreshStatuses((prev) => {
      const next = new Map(prev);
      for (const [id, newStatus] of entries) {
        const prevStatus = prev.get(id);
        if (newStatus.status === "idle" && prevStatus?.status === "running") {
          continue;
        }
        next.set(id, newStatus);
      }
      return next;
    });
  }, []);

  useEffect(() => {
    getValueDefinitions().then(setDefinitions);
    getFxRates()
      .then((r) => setFxRates(r.rates))
      .catch(() => undefined);
  }, []);

  useEffect(() => {
    if (pid) listCompanies(pid).then((list) => {
      setCompanies(list);
      pollStatuses(list);
    });
  }, [pid, pollStatuses]);

  const reloadAvailability = useCallback(async () => {
    if (companies.length === 0) {
      setAvailabilityMap(new Map());
      return;
    }
    const map = new Map<string, FyAvailability>();
    await Promise.all(
      companies.map(async (c) => {
        try {
          map.set(c.id, await getFyAvailability(c.id));
        } catch {
          map.set(c.id, { fy_years_with_data: [], keys_per_year: {}, has_snapshot_market_cap: false, is_us: false, annual_report_years: [], quarter_years: [], quarter_years_in_progress: [] });
        }
      })
    );
    setAvailabilityMap(map);
  }, [companies]);

  useEffect(() => {
    reloadAvailability();
  }, [reloadAvailability]);

  useEffect(() => {
    setNotFound(new Set());
    loadAllValues();
  }, [loadAllValues]);

  useEffect(() => {
    const anyRunning = Array.from(refreshStatuses.values()).some((s) => s.status === "running");
    if (!anyRunning) return;
    const timer = setInterval(() => pollStatuses(companies), 2000);
    return () => clearInterval(timer);
  }, [refreshStatuses, companies, pollStatuses]);

  const toggleCategory = (cat: string) => {
    setExpandedSections((prev) => {
      const next = new Set(prev);
      next.has(cat) ? next.delete(cat) : next.add(cat);
      return next;
    });
  };

  const handleRefreshCompany = async (c: Company, stammdatenOnly: boolean = false) => {
    const allApiKeys = definitions.filter((d) => d.source_type === "API").map((d) => d.key);
    // Stammdaten-Only ("Daily Numbers"): nur Live-API-Stammdaten-Keys
    // (Market Cap, Stock Price, Shares Outstanding). Backend filtert
    // zusaetzlich auf ALWAYS_CURRENT_KEYS — Frontend-Filter ist Optimierung
    // um nicht alle Keys ueber den Wire zu schicken.
    const stammdatenKeys = new Set(
      definitions.filter((d) => d.category === "STAMMDATEN" && d.source_type === "API").map((d) => d.key)
    );
    const apiKeys = stammdatenOnly
      ? allApiKeys.filter((k) => stammdatenKeys.has(k))
      : allApiKeys;
    // Pre-Refresh-Warnung: zeig dem User welche Manual-Werte zerstoert werden.
    // Bei Stammdaten-Only nur Stammdaten-Manuals warnen.
    const cRows = valuesMap.get(c.id) ?? [];
    const manualKeys = cRows
      .filter((r) => r.manually_overridden && apiKeys.includes(r.value_key))
      .map((r) => {
        const def = definitions.find((d) => d.key === r.value_key);
        return def?.label_de ?? r.value_key;
      });
    if (manualKeys.length > 0) {
      const ok = confirm(
        `${c.name}: ${manualKeys.length} manuell ueberschriebene Werte werden beim Refresh wieder ueberschrieben:\n\n` +
        manualKeys.map((k) => `  • ${k}`).join("\n") +
        `\n\nFortfahren?`
      );
      if (!ok) return;
    }
    setRefreshStatuses((prev) => new Map(prev).set(c.id, { company_id: c.id, total: apiKeys.length, completed: 0, current_key: null, status: "running" as const }));
    try {
      const updated = await refreshValues(c.id, apiKeys, period.value, period.year, stammdatenOnly);
      setValuesMap((prev) => {
        const next = new Map(prev);
        const existing = next.get(c.id) ?? [];
        const merged = new Map(existing.map((v) => [`${v.value_key}:${v.period_type}:${v.period_year}`, v]));
        for (const u of updated) merged.set(`${u.value_key}:${u.period_type}:${u.period_year}`, u);
        next.set(c.id, Array.from(merged.values()));
        return next;
      });
      const returnedKeys = new Set(updated.map((u) => u.value_key));
      setNotFound((prev) => {
        const next = new Set(prev);
        for (const k of apiKeys) {
          const nfKey = `${c.id}:${k}`;
          returnedKeys.has(k) ? next.delete(nfKey) : next.add(nfKey);
        }
        return next;
      });
    } catch (err) {
      console.error(`Refresh failed for ${c.name}:`, err);
    } finally {
      await pollStatuses(companies);
      await loadAllValues();
    }
  };

  const getVal = (companyId: string, key: string): CompanyValue | undefined =>
    (valuesMap.get(companyId) ?? []).find((v) => v.value_key === key);

  const convertCurrency = (val: number | string | null, from: string | null): number | null => {
    if (val == null) return null;
    const num = typeof val === "string" ? parseFloat(val) : val;
    if (isNaN(num)) return null;
    if (!from) return num;
    const f = fxRates[from];
    const t = fxRates[displayCurrency];
    if (f === undefined || t === undefined) return null;
    return f === t ? num : (num / f) * t;
  };

  const isCumMode = period.value === "CUM";
  const showPrevYear = period.value === "FY" && period.year !== undefined;
  const prevYear = showPrevYear ? (period.year as number) - 1 : null;

  const HIDDEN_IN_CUM_SECTIONS = new Set(["STAMMDATEN"]);
  // Im Estimate-Modus (laufende FY) gibt's noch keinen FY-Ende-MCap → actual_return
  // ist immer null und sollte gar nicht angezeigt werden.
  const isEstimateMode = period.value === "FY"
    && period.year !== undefined
    && period.year >= new Date().getFullYear();
  const grouped = CATEGORY_ORDER.filter((cat) => !(isCumMode && HIDDEN_IN_CUM_SECTIONS.has(cat))).map((cat) => {
    const allDefsRaw = definitions.filter((d) => d.category === cat).sort((a, b) => a.sort_order - b.sort_order);
    const allDefs = isEstimateMode ? allDefsRaw.filter((d) => d.key !== "actual_return") : allDefsRaw;
    const factorDefs = allDefs.filter((d) => FACTOR_KEYS.has(d.key));
    const inputDefs = allDefs.filter((d) => !FACTOR_KEYS.has(d.key));
    const isExpanded = !isCumMode && expandedSections.has(cat);
    // actual_return ist FY-spezifisch (Yahoo MCap-Anker), CUM rechnet über mehrere FYs.
    const cumFactorDefs = factorDefs.filter((d) => d.key !== "market_cap" && d.key !== "actual_return");
    // Wenn die Kategorie KEINE Faktoren hat (z.B. FCF nach Verschiebung von fcf_yield
    // -> VALUATION), zeige Inputs direkt — sonst sieht User leere Spalte + 'einklappbar'.
    const hasFactors = factorDefs.length > 0;
    const baseVisibleDefs = isCumMode ? cumFactorDefs : (isExpanded || !hasFactors ? allDefs : factorDefs);
    const visibleSectionDefs: (ValueDefinition & { isPrevYear?: boolean; basedOnKey?: string })[] = [];
    for (const d of baseVisibleDefs) {
      visibleSectionDefs.push(d);
      if (showPrevYear && isExpanded && PREV_YEAR_DISPLAY_KEYS.has(d.key)) {
        visibleSectionDefs.push({
          ...d,
          key: `${d.key}__prev`,
          label_en: `${d.label_en} (FY${prevYear})`,
          label_de: `${d.label_de} (FY${prevYear})`,
          isPrevYear: true,
          basedOnKey: d.key,
        });
      }
    }
    return {
      category: cat,
      label: CATEGORY_LABELS[cat],
      defs: visibleSectionDefs,
      // Wenn die Kategorie keine Faktoren hat, sind die Inputs immer sichtbar
      // -> nichts zum Einklappen anzeigen.
      hiddenInputCount: (isCumMode || !hasFactors) ? 0 : inputDefs.length,
      isExpanded,
      isEmptyInCompact: factorDefs.length === 0,
    };
  }).filter((g) => g.defs.length > 0 || g.hiddenInputCount > 0);

  const visibleDefs = grouped.flatMap((g) => g.defs);

  // Vertikale Kategorie-Trenner: jeder letzte Key in einer Gruppe bekommt
  // einen dickeren rechten Rand.
  const lastKeyInGroup = new Set<string>();
  for (const g of grouped) {
    if (g.defs.length > 0) lastKeyInGroup.add(g.defs[g.defs.length - 1].key);
  }
  const groupSep = (key: string): string =>
    lastKeyInGroup.has(key) ? " !border-r-2 !border-foreground/25" : "";

  if (!user) return null;

  return (
    <div className="min-h-screen bg-background">
      <AppHeader email={user.email} onLogout={logout} />
      <main className="p-6">
        <div className="mb-4">
          <Link to="/" className="inline-flex items-center gap-1 text-sm text-muted-foreground transition-colors hover:text-foreground">
            <ChevronLeft className="h-3.5 w-3.5" />
            {t.portfolios}
          </Link>
        </div>

        <div className="mb-4 flex flex-wrap items-end justify-between gap-4">
          <div className="flex items-center gap-3">
            <h2 className="text-2xl font-semibold tracking-tight text-foreground">{t.dashboard}</h2>
          </div>
          <div className="flex items-center gap-3">
            <div className="flex flex-wrap items-center gap-3">
              <div className="flex items-center gap-1">
                <span className="mr-1 text-[10px] font-semibold uppercase tracking-wide text-muted-foreground">Estimate</span>
                {FY_OPTIONS.filter((p) => p.year >= new Date().getFullYear()).map((p) => {
                  const i = FY_OPTIONS.indexOf(p);
                  const isActive = periodMode === "FY" && fyIdx === i;
                  return (
                    <button key={`est-${i}`}
                      onClick={() => { setPeriodMode("FY"); setFyIdx(i); }}
                      title="Laufendes FY — Werte basieren auf Q-Faktor-Schätzungen aus Quartalsberichten"
                      className={`rounded-md px-2 py-1 text-[11px] font-medium transition-colors ${isActive ? "bg-violet-600 text-white" : "border border-violet-300 bg-violet-50 text-violet-800 hover:bg-violet-100"}`}
                    >{p.label.replace("FY ", "")}</button>
                  );
                })}
              </div>
              <div className="h-6 w-px bg-border" />
              <div className="flex items-center gap-1">
                <span className="mr-1 text-[10px] font-semibold uppercase tracking-wide text-muted-foreground">Abgeschlossen</span>
                {FY_OPTIONS.filter((p) => p.year < new Date().getFullYear()).map((p) => {
                  const i = FY_OPTIONS.indexOf(p);
                  const isActive = periodMode === "FY" && fyIdx === i;
                  return (
                    <button key={`hist-${i}`}
                      onClick={() => { setPeriodMode("FY"); setFyIdx(i); }}
                      className={`rounded-md px-2 py-1 text-[11px] font-medium transition-colors ${isActive ? "bg-primary text-primary-foreground" : "border border-border bg-background text-foreground hover:bg-muted"}`}
                    >{p.label.replace("FY ", "")}</button>
                  );
                })}
              </div>
              <div className="h-6 w-px bg-border" />
              <div className="flex items-center gap-1">
                <span className="mr-1 text-[10px] font-semibold uppercase tracking-wide text-muted-foreground">Kumuliert</span>
                {CUM_OPTIONS.map((p, i) => {
                  const isActive = periodMode === "CUM" && cumIdx === i;
                  let missingCount = 0;
                  for (const c of companies) {
                    const av = availabilityMap.get(c.id);
                    if (!av?.has_snapshot_market_cap) { missingCount++; continue; }
                    for (let y = p.from_year - 1; y <= p.to_year; y++) {
                      const isPre = y === p.from_year - 1;
                      const requiredKeys = isPre ? ["net_income"] : CUM_INPUT_FY_KEYS;
                      const have = new Set(av.keys_per_year?.[String(y)] ?? []);
                      if (requiredKeys.some((k) => !have.has(k))) { missingCount++; break; }
                    }
                  }
                  const isUnavailable = companies.length > 0 && missingCount > 0;
                  return (
                    <button key={i}
                      onClick={() => { setPeriodMode("CUM"); setCumIdx(i); }}
                      title={isUnavailable ? `${missingCount}/${companies.length} Firmen brauchen Refresh` : `Kumuliert FY${p.from_year} bis FY${p.to_year}`}
                      className={`rounded-md px-2 py-1 text-[11px] font-medium transition-colors ${isActive ? "bg-primary text-primary-foreground" : "border border-border bg-background text-foreground hover:bg-muted"} ${isUnavailable && !isActive ? "opacity-50" : ""}`}
                    >
                      {p.label}
                      {isUnavailable && <span className="ml-1 text-amber-600">⚠</span>}
                    </button>
                  );
                })}
              </div>
            </div>
            <div className="flex items-center gap-1.5">
              <select value={displayCurrency} onChange={(e) => setDisplayCurrency(e.target.value)}
                className="rounded-md border border-input bg-background px-3 py-1.5 text-sm text-foreground">
                {CURRENCIES.map((c) => <option key={c} value={c}>{c}</option>)}
              </select>
            </div>
          </div>
        </div>

        <div className="mb-4 flex items-center gap-3 rounded-lg border border-border/60 bg-muted/30 px-4 py-2.5">
          <div className="flex items-center gap-2">
            <div className="h-2.5 w-2.5 rounded-full bg-primary" />
            <span className="text-sm font-medium text-foreground">{period.label}</span>
          </div>
          <span className="text-xs text-muted-foreground">|</span>
          <span className="text-xs text-muted-foreground">
            {period.value === "CUM"
              ? `Kumuliert über ${period.to_year - period.from_year + 1} FYs (${period.from_year}-${period.to_year}) · MCap-Anker: Anfang FY${period.from_year} (= Ende FY${period.from_year - 1}) · Cell zeigt Σ über die Periode + p.a.-Durchschnitt`
              : `Finanzdaten: Geschäftsjahr ${period.year} · MCap-Anker: Anfang FY (= Ende FY${(period.year ?? 0) - 1})`}
          </span>
          {isLoadingPeriod && (
            <span className="flex items-center gap-1.5 text-xs text-primary">
              <Loader2 className="h-3.5 w-3.5 animate-spin" />
              Lade Daten…
            </span>
          )}
          {/* GAAP/Non-GAAP Toggle — wirkt auf Net Income, EBITDA, FCF und
              kaskadiert auf P/E, EV/EBITDA, FCF-Yield, NI-Growth, H-Return. */}
          <div className="ml-auto inline-flex items-center rounded-md border border-border bg-card p-0.5"
               title="Schaltet zwischen Reported (GAAP/IFRS) und Adjusted (Non-GAAP/Underlying). Tooltip zeigt immer beide Werte.">
            <button
              onClick={() => toggleValuationMode("reported")}
              className={`rounded px-2.5 py-1 text-[11px] font-medium transition-colors ${
                valuationMode === "reported"
                  ? "bg-primary text-primary-foreground"
                  : "text-muted-foreground hover:text-foreground"
              }`}
            >Reported</button>
            <button
              onClick={() => toggleValuationMode("adjusted")}
              className={`rounded px-2.5 py-1 text-[11px] font-medium transition-colors ${
                valuationMode === "adjusted"
                  ? "bg-amber-100 text-amber-900"
                  : "text-muted-foreground hover:text-foreground"
              }`}
            >Adjusted</button>
          </div>
        </div>

        {period.value === "FY" && period.year !== undefined && period.year < new Date().getFullYear() && (() => {
          const locked = companies.filter((c) => isHohnLocked(availabilityMap.get(c.id), period.year));
          if (locked.length === 0) return null;
          return (
            <div className="mb-4 rounded-lg border border-amber-300 bg-amber-50 px-4 py-3 text-xs text-amber-900">
              <div className="flex items-center gap-2 font-semibold">
                <Lock className="h-4 w-4 text-amber-600" />
                H-Return gesperrt für {locked.length} {locked.length === 1 ? "Firma" : "Firmen"} (FY{period.year})
              </div>
              <p className="mt-1 text-[11px] text-amber-800/90">
                Bei Non-US-Unternehmen ist die H-Return für abgeschlossene FYs nur berechenbar, wenn ein
                fertig extrahierter Annual Report vorliegt. Lade den Geschäftsbericht in der Firmen-Verwaltung hoch.
              </p>
              <div className="mt-2 flex flex-wrap gap-1.5">
                {locked.map((c) => (
                  <Link key={c.id} to={`/portfolios/${pid}/manage`}
                    className="rounded border border-amber-200 bg-white/70 px-2 py-0.5 font-mono text-[11px] font-medium text-amber-900 hover:bg-white"
                    title="Annual Report hochladen"
                  >
                    {c.ticker}
                  </Link>
                ))}
              </div>
            </div>
          );
        })()}

        {period.value === "CUM" && (() => {
          type Gap = { company: Company; mcapMissing: boolean; missingPerYear: { year: number; isPre: boolean; missingKeys: string[] }[] };
          const gaps: Gap[] = [];
          for (const c of companies) {
            const av = availabilityMap.get(c.id);
            const mcapMissing = !av?.has_snapshot_market_cap;
            const missingPerYear: Gap["missingPerYear"] = [];
            for (let y = period.from_year - 1; y <= period.to_year; y++) {
              const isPre = y === period.from_year - 1;
              const required = isPre ? ["net_income"] : CUM_INPUT_FY_KEYS;
              const have = new Set(av?.keys_per_year?.[String(y)] ?? []);
              const miss = required.filter((k) => !have.has(k));
              if (miss.length > 0) missingPerYear.push({ year: y, isPre, missingKeys: miss });
            }
            if (mcapMissing || missingPerYear.length > 0) {
              gaps.push({ company: c, mcapMissing, missingPerYear });
            }
          }
          if (gaps.length === 0) return null;
          return (
            <div className="mb-4 rounded-lg border border-amber-300 bg-amber-50 px-4 py-3 text-xs text-amber-900">
              <div className="flex items-center gap-2 font-semibold">
                <AlertTriangle className="h-4 w-4 text-amber-600" />
                Daten unvollständig — {gaps.length} von {companies.length} Firmen brauchen Refresh
              </div>
              <p className="mt-1 text-[11px] text-amber-800/90">
                Pre-Period FY{period.from_year - 1} braucht nur <code className="rounded bg-amber-100 px-1">net_income</code>. Periode FY{period.from_year}-{period.to_year} braucht alle 10 Cum-Inputs (FCF, Net Income, SBC, Buybacks, Dividends, Cash/Marketable, Leases, LT-Debt). MCap kommt aus SNAPSHOT.
              </p>
              <div className="mt-2 space-y-2">
                {gaps.map(({ company, mcapMissing, missingPerYear }) => (
                  <div key={company.id} className="rounded border border-amber-200 bg-white/60 p-2">
                    <div className="flex items-start justify-between gap-2">
                      <div className="flex-1 min-w-0">
                        <div className="text-xs font-semibold text-amber-900">
                          {company.name} <span className="text-amber-700">({company.ticker})</span>
                        </div>
                        {mcapMissing && (
                          <div className="mt-1 text-[11px] text-amber-800">
                            SNAPSHOT: <span className="font-mono">market_cap</span> fehlt
                          </div>
                        )}
                        {missingPerYear.map(({ year, isPre, missingKeys }) => (
                          <div key={year} className="mt-1 text-[11px] text-amber-800">
                            <span className="font-mono font-semibold">FY{year}</span>
                            {isPre && <span className="text-amber-700"> (pre-period)</span>}:{" "}
                            <span className="font-mono">{missingKeys.join(", ")}</span>
                          </div>
                        ))}
                      </div>
                      <button
                        onClick={async () => {
                          if (mcapMissing) {
                            try { await refreshValues(company.id, ["market_cap"], "SNAPSHOT"); } catch {}
                          }
                          for (const { year, missingKeys } of missingPerYear) {
                            try {
                              await refreshValues(company.id, missingKeys, "FY", year);
                            } catch {}
                          }
                          await loadAllValues();
                        }}
                        className="shrink-0 rounded bg-amber-600 px-2.5 py-1 text-[11px] font-medium text-white transition-colors hover:bg-amber-700"
                        title="Triggert gezielten Refresh nur für die fehlenden Werte"
                      >
                        Fehlende holen
                      </button>
                    </div>
                  </div>
                ))}
              </div>
            </div>
          );
        })()}

        {companies.some((c) => refreshStatuses.get(c.id)?.status === "running") && (
          <div className="mb-4 space-y-2">
            {companies
              .filter((c) => refreshStatuses.get(c.id)?.status === "running")
              .map((c) => (
                <RefreshProgressBar
                  key={c.id}
                  companyName={c.name}
                  status={refreshStatuses.get(c.id)!}
                />
              ))}
          </div>
        )}

        <div className="relative overflow-x-auto rounded-xl border border-border/60 bg-card">
          {isLoadingPeriod && valuesMap.size > 0 && (
            <div className="pointer-events-none absolute inset-0 z-30 flex items-start justify-center bg-white/40 pt-16">
              <span className="flex items-center gap-2 rounded-full border border-border bg-white/95 px-3 py-1.5 text-xs font-medium text-primary shadow-sm">
                <Loader2 className="h-3.5 w-3.5 animate-spin" />
                Lade {period.label}…
              </span>
            </div>
          )}
          {isLoadingPeriod && valuesMap.size === 0 && (
            <div className="flex items-center justify-center px-6 py-16">
              <div className="flex flex-col items-center gap-2 text-muted-foreground">
                <Loader2 className="h-6 w-6 animate-spin text-primary" />
                <span className="text-sm">Lade Werte für {period.label}…</span>
              </div>
            </div>
          )}
          <table className={`w-full text-sm ${isLoadingPeriod && valuesMap.size === 0 ? "hidden" : ""}`}>
            <thead>
              {/* Category header row */}
              <tr>
                <th className="sticky left-0 z-20 border-b border-r bg-card px-3 py-2 text-left text-xs font-semibold text-foreground" rowSpan={2}>
                  Firma
                </th>
                {grouped.map((g) => {
                  const colSpan = g.defs.length === 0 ? 1 : g.defs.length;
                  const canExpand = g.hiddenInputCount > 0;
                  return (
                    <th
                      key={g.category}
                      colSpan={colSpan}
                      className={`select-none border-b border-r px-3 py-2 text-center text-xs font-semibold uppercase tracking-wider transition-colors ${canExpand ? "cursor-pointer hover:opacity-80" : ""} ${CATEGORY_COLORS[g.category]}`}
                      onClick={canExpand ? () => toggleCategory(g.category) : undefined}
                      title={canExpand ? (g.isExpanded ? "Hilfswerte ausblenden" : `${g.hiddenInputCount} Hilfswerte einblenden`) : undefined}
                    >
                      <div className="flex items-center justify-center gap-1.5">
                        {canExpand && (g.isExpanded ? <ChevronDown className="h-3.5 w-3.5" /> : <ChevronRight className="h-3.5 w-3.5" />)}
                        <span>{g.label}</span>
                        {canExpand && !g.isExpanded && (
                          <span className="rounded-full bg-foreground/10 px-1.5 py-0.5 text-[9px] font-semibold leading-none">+{g.hiddenInputCount}</span>
                        )}
                      </div>
                    </th>
                  );
                })}
              </tr>
              {/* Value column headers (or empty placeholder for sections without factors in compact mode) */}
              <tr>
                {grouped.flatMap((g) => {
                  if (g.defs.length === 0) {
                    return [
                      <th key={`${g.category}-empty`}
                        className="border-b border-r-2 border-foreground/25 px-2 py-1.5 text-center text-[10px] italic text-muted-foreground">
                        nur Hilfswerte
                      </th>
                    ];
                  }
                  return g.defs.map((d) => (
                    <th key={d.key}
                      className={`whitespace-nowrap border-b border-r border-border/40 px-3 py-2 text-left text-xs font-medium text-muted-foreground ${d.isPrevYear ? "bg-muted/20 italic" : ""}${groupSep(d.key)}`}>
                      <span className="truncate" title={d.label_de}>{d.label_en}</span>
                    </th>
                  ));
                })}
              </tr>
            </thead>
            <tbody>
              {companies.flatMap((company) => {
                const cRows = valuesMap.get(company.id) ?? [];
                const cPrev = prevYearValuesMap.get(company.id) ?? [];
                const av = availabilityMap.get(company.id);
                // FY-Mode Mode-aware Recompute fuer Calculated Keys (Hohn-Rendite,
                // NI-Growth, P/E, EV/EBITDA, FCF-Yield, etc.). Backend persistiert
                // diese mit Reported-Inputs; im Adjusted-Mode muessen sie frontend-
                // side neu gerechnet werden damit Hohn-Rendite kaskadiert.
                const fyModeRecomputed = (period.value === "FY" && !isEstimateMode && valuationMode === "adjusted")
                  ? buildVariantValues(cRows, cPrev, "fy", valuationMode)
                  : null;
                const estLocked = isEstimateMode && isEstimateLocked(av, period.year);
                const fyHistLocked = period.value === "FY" && !isEstimateMode && isFyHistoricalLocked(av, period.year);
                const totalCols = visibleDefs.length + grouped.filter((g) => g.defs.length === 0).length + 1;

                // FY-historical + kein Annual Report -> ganze Zeile locked
                // (analog Estimate-Lock — User soll AR hochladen, nicht per
                // Cell manuell recherchieren).
                if (fyHistLocked) {
                  return [(
                    <tr key={`${company.id}-fy-locked`} className="border-t-4 border-border bg-amber-50/70">
                      <td className="sticky left-0 z-10 whitespace-nowrap border-r bg-amber-50 px-3 py-3 align-middle">
                        <div className="flex flex-col items-start gap-1">
                          <div className="flex items-center gap-1.5">
                            <span className="font-semibold text-foreground">{company.name}</span>
                            <span className="rounded bg-primary/10 px-1.5 py-0.5 font-mono text-[10px] font-medium text-primary">{company.ticker}</span>
                          </div>
                          <span className="text-[10px] text-amber-700">Aktion: Annual Report hochladen</span>
                        </div>
                      </td>
                      <td colSpan={totalCols - 1} className="px-4 py-3">
                        <div className="flex items-center gap-2 text-amber-800">
                          <Lock className="h-4 w-4 shrink-0" />
                          <span className="text-sm font-medium">
                            Annual Report für FY{period.year} fehlt — bitte hochladen
                          </span>
                        </div>
                        <p className="mt-1 text-xs text-amber-700/80">
                          Für abgeschlossene FY-Jahre kommen die Werte aus dem hochgeladenen Annual Report.
                          Fehlt ein Wert im PDF, fällt das System automatisch auf Web-Recherche (Claude) zurück.
                        </p>
                      </td>
                    </tr>
                  )];
                }

                // Estimate-Mode locked -> Lock-Row wenn FY[N-1]-Anker fehlt
                if (estLocked) {
                  const targetYear = period.year ?? new Date().getFullYear();
                  const prevYear = targetYear - 1;
                  const action = "Annual Report fuer FY[N-1] hochladen";
                  const headline = `Estimate gesperrt — bitte Annual Report für FY${prevYear} hochladen`;
                  const subline = `Ohne FY${prevYear}-Basisdaten hat die Web-Recherche keinen Anker für die Schätzung von FY${targetYear} (NI-Growth, Net-Debt-Change brauchen Vorjahres-Werte).`;
                  const isMissingFy = true;
                  return [(
                    <tr key={`${company.id}-est-locked`} className="border-t-4 border-border bg-amber-50/70">
                      <td className="sticky left-0 z-10 whitespace-nowrap border-r bg-amber-50 px-3 py-3 align-middle">
                        <div className="flex flex-col items-start gap-1">
                          <div className="flex items-center gap-1.5">
                            <span className="font-semibold text-foreground">{company.name}</span>
                            <span className="rounded bg-primary/10 px-1.5 py-0.5 font-mono text-[10px] font-medium text-primary">{company.ticker}</span>
                          </div>
                          <span className="text-[10px] text-amber-700">Aktion: {action}</span>
                        </div>
                      </td>
                      <td colSpan={totalCols - 1} className="px-4 py-3">
                        <div className="flex items-center gap-2 text-amber-800">
                          <Lock className="h-4 w-4 shrink-0" />
                          <span className="text-sm font-medium">{headline}</span>
                        </div>
                        <p className="mt-1 text-xs text-amber-700/80">{subline}</p>
                        {!isMissingFy && (() => {
                          const inProgress = av?.quarter_years_in_progress ?? [];
                          if (inProgress.includes(prevYear)) {
                            return (
                              <p className="mt-1 text-xs font-medium text-amber-900">
                                ⓘ Es gibt bereits Q-Reports für FY{prevYear}, die Extraktion ist aber noch nicht fertig (PENDING/EXTRACTING/FAILED). Status im IR-Documents-Bereich prüfen.
                              </p>
                            );
                          }
                          return null;
                        })()}
                      </td>
                    </tr>
                  )];
                }

                // Q-Faktor entfernt — Estimate-Mode nutzt den normalen Single-Row-
                // Pfad. Werte kommen aus Web-Recherche (Claude+Gemini) und/oder
                // Q-PDF-Guidance, beides ueber den Standard-Cell-Renderer.
                return [(
                <tr key={company.id} className="border-b border-border/30 last:border-b-0 hover:bg-muted/20">
                  <td className="sticky left-0 z-10 whitespace-nowrap border-r bg-card px-3 py-2 font-medium text-foreground">
                    {(() => {
                      const av = availabilityMap.get(company.id);
                      const isRunning = refreshStatuses.get(company.id)?.status === "running";
                      const hasAnyAR = (av?.annual_report_years.length ?? 0) > 0;
                      const isUS = av?.is_us ?? false;
                      const canCompute = isUS || hasAnyAR;
                      const disabledReason = canCompute
                        ? null
                        : "Lade zuerst mindestens einen Annual Report hoch (Non-US-Firma).";
                      // Im Estimate-Mode (laufendes FY) zeigen wir zwei Buttons:
                      // 1) Vollberechnung mit Web-Recherche fuer Forecasts
                      // 2) Nur Stammdaten (Live-MCap/Stock-Price) — taeglicher Quick-Refresh
                      const isEstimateRow = period.value === "FY"
                        && period.year !== undefined
                        && period.year >= new Date().getFullYear();
                      return (
                        <div className="flex items-center gap-2">
                          <span>{company.name}</span>
                          <span className="rounded bg-primary/10 px-1.5 py-0.5 font-mono text-[10px] font-medium text-primary">
                            {company.ticker}
                          </span>
                          <button
                            onClick={() => handleRefreshCompany(company, false)}
                            disabled={isRunning || !canCompute}
                            title={disabledReason ?? "Vollberechnung: alle Fundamentals via Web-Recherche neu holen (teuer, mehrere Minuten)"}
                            className="ml-1 inline-flex shrink-0 items-center gap-1 rounded-md border border-primary/40 bg-primary/5 px-2 py-1 text-[11px] font-medium text-primary transition-colors hover:bg-primary/10 disabled:cursor-not-allowed disabled:opacity-40 disabled:hover:bg-primary/5"
                          >
                            <RefreshCw className={`h-3 w-3 ${isRunning ? "animate-spin" : ""}`} />
                            {isRunning ? "Berechnet…" : (isEstimateRow ? "Vollberechnung" : "Werte berechnen")}
                          </button>
                          {isEstimateRow && (
                            <button
                              onClick={() => handleRefreshCompany(company, true)}
                              disabled={isRunning || !canCompute}
                              title="Daily Numbers: nur Live-Stammdaten (Market Cap, Stock Price, Shares) per API neu holen — schnell, keine Web-Recherche"
                              className="inline-flex shrink-0 items-center gap-1 rounded-md border border-emerald-600/40 bg-emerald-50 px-2 py-1 text-[11px] font-medium text-emerald-700 transition-colors hover:bg-emerald-100 disabled:cursor-not-allowed disabled:opacity-40 disabled:hover:bg-emerald-50"
                            >
                              <RefreshCw className={`h-3 w-3 ${isRunning ? "animate-spin" : ""}`} />
                              Daily Numbers
                            </button>
                          )}
                        </div>
                      );
                    })()}
                  </td>
                  {grouped.flatMap((g) => {
                    if (g.defs.length === 0) {
                      return [
                        <td key={`${company.id}-${g.category}-empty`}
                          className="border-r-2 border-foreground/25 px-2 py-2 text-center text-muted-foreground/30">
                          —
                        </td>
                      ];
                    }
                    return g.defs.map((d) => {
                      if (d.isPrevYear) {
                        const baseKey = d.basedOnKey as string;
                        const prevRows = prevYearValuesMap.get(company.id) ?? [];
                        const prevCv = prevRows.find((v) => v.value_key === baseKey);
                        // Mode-aware Prev-Year-Wert (Reported vs Adjusted) — sonst
                        // ist FY-1-Anzeige inkonsistent zu NI-Growth-Berechnung.
                        const prevRaw = prevCv ? _primaryByMode(prevCv, valuationMode) : null;
                        const prevValid = prevRaw != null && !isNaN(prevRaw) ? prevRaw : null;
                        const shouldConvertPrev = d.is_currency && d.data_type === "NUMERIC" && prevCv?.currency;
                        const convertedPrev = shouldConvertPrev ? convertCurrency(prevValid, prevCv?.currency ?? null) : prevValid;
                        const fxUnknownPrev = shouldConvertPrev && prevValid !== null && convertedPrev === null;
                        const displayPrev = fxUnknownPrev ? prevValid : convertedPrev;
                        return (
                          <td key={`${company.id}-${d.key}`}
                            className={`whitespace-nowrap border-r border-border/40 bg-muted/20 px-3 py-2 tabular text-muted-foreground${groupSep(d.key)}`}
                            title={`Vorjahres-Wert ${d.label_de}`}
                          >
                            <span className="font-mono text-sm italic">
                              {prevValid == null ? "—" : formatValue(displayPrev, d.unit, displayCurrency)}
                            </span>
                          </td>
                        );
                      }
                      if (isCumMode) {
                        const cumResp = cumulativeMap.get(company.id);
                        const cell = cumResp?.values?.[d.key];
                        const cumVal = cell?.cum != null ? parseFloat(cell.cum) : null;
                        const avgVal = cell?.pa_avg != null ? parseFloat(cell.pa_avg) : null;
                        const isPartial = (cell?.missing?.length ?? 0) > 0;
                        const tier = colorTier(d.key, avgVal);
                        const tierBg = isPartial ? "bg-amber-50/40" : (tier ? TIER_BG[tier] : "");
                        const fmt = (n: number | null) => n == null || isNaN(n) ? "—" : `${n.toFixed(2)} %`;
                        const fmtPa = (n: number | null) => n == null || isNaN(n) ? "—" : `${n.toFixed(2)} % p.a.`;
                        return (
                          <td key={`${company.id}-${d.key}`}
                            className={`whitespace-nowrap border-r border-border/40 px-3 py-2 tabular cursor-pointer hover:bg-muted/30 ${tierBg}${groupSep(d.key)}`}
                            title={isPartial ? `Partial — fehlt: ${cell?.missing.join("; ")}` : `${d.label_de} (Kumuliert ${period.from_year}-${period.to_year})`}
                            onClick={() => {
                              if (!cumResp) return;
                              setCumDrawer({
                                companyId: company.id,
                                companyName: company.name,
                                valueKey: d.key,
                                valueLabel: `${d.label_en} (Kumuliert ${period.from_year}-${period.to_year})`,
                              });
                              setCumDrawerOpen(true);
                            }}
                          >
                            <div className="flex flex-col gap-0.5">
                              <div className="flex items-center gap-1">
                                <span className="font-mono text-sm font-semibold text-foreground">{fmt(cumVal)}</span>
                                {isPartial && <AlertTriangle className="h-3 w-3 text-amber-600" />}
                              </div>
                              <span className="text-[10px] text-muted-foreground">{fmtPa(avgVal)}</span>
                            </div>
                          </td>
                        );
                      }
                      const av = availabilityMap.get(company.id);
                      const hohnLockedHere = HOHN_LOCKED_KEYS.has(d.key)
                        && period.value === "FY"
                        && isHohnLocked(av, period.year);
                      if (hohnLockedHere) {
                        return (
                          <td key={`${company.id}-${d.key}`}
                            className={`whitespace-nowrap border-r border-border/40 bg-amber-50/70 px-3 py-2 cursor-help${groupSep(d.key)}`}
                            title={`Hohn-Rendite gesperrt — kein Annual Report für FY${period.year} hochgeladen. Lade den Geschäftsbericht hoch, dann lassen sich Werte verifizieren und Hohn-Rendite berechnen.`}
                          >
                            <div className="flex items-center gap-1.5 text-amber-800">
                              <Lock className="h-3.5 w-3.5 shrink-0" />
                              <span className="text-xs font-medium">Annual Report fehlt</span>
                            </div>
                          </td>
                        );
                      }

                      const cv = getVal(company.id, d.key);
                      // Mode-aware Wert-Auswahl (Reported vs Adjusted) mit Fallback-Info
                      const modeVal = cv ? getModeValue(cv, valuationMode) : { value: null, isAdjustedActive: false, isFallbackToReported: false };
                      // FY-Mode + Adjusted: Calculated-Keys (Hohn-Rendite, NI-Growth,
                      // P/E etc.) frontend-side neu gerechnet aus Adjusted-Inputs.
                      const isCalcKey = d.source_type === "CALCULATED" && d.key !== "actual_return";
                      const recomputedFY = (fyModeRecomputed && isCalcKey) ? fyModeRecomputed.get(d.key) ?? null : null;
                      const raw: number | null = recomputedFY != null
                        ? recomputedFY
                        : (modeVal.value != null && !isNaN(modeVal.value) ? modeVal.value : null);
                      const rawValid = raw;
                      const shouldConvert = d.is_currency && d.data_type === "NUMERIC" && cv?.currency;
                      const convertedVal = shouldConvert ? convertCurrency(rawValid, cv?.currency ?? null) : rawValid;
                      const fxUnknown = shouldConvert && rawValid !== null && convertedVal === null;
                      const displayVal = fxUnknown ? rawValid : convertedVal;
                      const isQualitative = d.source_type === "QUALITATIVE";
                      const isCalculated = d.source_type === "CALCULATED";

                      const isHistoricalQual = isQualitative && period.value === "FY";

                      const isEditing = editCell?.companyId === company.id && editCell?.key === d.key;

                      const handleSaveEdit = async () => {
                        if (!editCell || saving) return;
                        const num = parseNumericInput(editCell.value);
                        if (isNaN(num)) { setEditCell(null); return; }
                        setSaving(true);
                        const defForKey = definitions.find((def) => def.key === d.key);
                        const effPeriodType = (isQualitative || defForKey?.always_current) ? "SNAPSHOT" : period.value;
                        const effPeriodYear = (isQualitative || defForKey?.always_current) ? undefined : period.year;
                        try {
                          await overrideValue(company.id, d.key, { numeric_value: num, source_name: "Manuell" }, effPeriodType, effPeriodYear);
                          await loadAllValues();
                        } finally {
                          setSaving(false);
                          setEditCell(null);
                        }
                      };

                      const fyTier = colorTier(d.key, displayVal);
                      const fyTierBg = fyTier ? TIER_BG[fyTier] : "";
                      const isStammdatenKey = d.category === "STAMMDATEN";
                      const showFyAsOf = isStammdatenKey && cv?.period_type === "FY" && cv?.period_year != null;
                      // Try parsing as-of date from source_name (e.g. "Adj Close 30.09.2025") — most reliable.
                      // Fallback: FY-Ende des Vorjahres (Stammdaten = Anker am letzten Tag von FY[N-1]).
                      const sourceDateMatch = cv?.source_name?.match(/(\d{2}\.\d{2}\.\d{4})/);
                      const fyAsOfBadge = showFyAsOf
                        ? (sourceDateMatch?.[1]
                            ?? (company.fiscal_year_end_day && company.fiscal_year_end_month && cv?.period_year != null
                                ? `${String(company.fiscal_year_end_day).padStart(2, "0")}.${String(company.fiscal_year_end_month).padStart(2, "0")}.${cv.period_year - 1}`
                                : `FY${cv?.period_year}`))
                        : null;
                      // Partial-Hohn detect: if any required component for hohn_return_simple/detailed is missing
                      let fyPartialMissing: string[] = [];
                      if (period.value === "FY" && (d.key === "hohn_return_simple" || d.key === "hohn_return_detailed")) {
                        const requiredKeys = d.key === "hohn_return_simple"
                          ? ["fcf_yield", "ni_growth", "sbc_yield", "net_debt_change_pct"]
                          : ["dividend_yield", "ni_growth", "net_buyback_yield", "net_debt_change_pct"];
                        fyPartialMissing = requiredKeys.filter((k) => {
                          const c = getVal(company.id, k);
                          const v = c?.numeric_value;
                          return v == null || (typeof v === "string" && v === "");
                        });
                      }
                      const fyIsPartial = fyPartialMissing.length > 0 && cv?.numeric_value != null;

                      const sourceTooltip = cv?.source_name
                        ? `Quelle: ${cv.source_name}${cv.source_link ? ` (${cv.source_link})` : ""}`
                        : undefined;
                      return (
                        <td key={`${company.id}-${d.key}`}
                          className={`whitespace-nowrap border-r border-border/40 px-3 py-2 tabular ${isCalculated ? "" : "hover:bg-muted/30"} ${isHistoricalQual ? "bg-amber-50/50" : ""} ${isCalculated && !fyTier ? "bg-muted/10" : ""} ${fyTierBg} ${fyIsPartial ? "bg-amber-50/40" : ""}${groupSep(d.key)}`}
                          title={fyIsPartial ? `Partial — fehlende Komponenten: ${fyPartialMissing.join(", ")}` : (isCalculated ? "Berechneter Wert (Formel)" : sourceTooltip)}
                          onDoubleClick={isCalculated ? undefined : (e) => {
                            e.stopPropagation();
                            const currentVal = cv?.numeric_value != null ? String(cv.numeric_value) : "";
                            setEditCell({ companyId: company.id, key: d.key, value: currentVal });
                          }}
                        >
                          {isEditing ? (
                            <input
                              autoFocus
                              type="text"
                              className="w-20 rounded border border-primary bg-background px-1.5 py-0.5 font-mono text-sm text-foreground outline-none"
                              value={editCell.value}
                              onChange={(e) => setEditCell({ ...editCell, value: e.target.value })}
                              onKeyDown={(e) => {
                                if (e.key === "Enter") handleSaveEdit();
                                if (e.key === "Escape") setEditCell(null);
                              }}
                              onBlur={handleSaveEdit}
                            />
                          ) : (
                          <div className="flex items-center gap-1.5">
                            {isQualitative && (
                              <Sparkles className="h-3 w-3 shrink-0 text-primary/60" />
                            )}
                            {(() => {
                              const pdfNullSource = cv?.numeric_value == null && cv?.text_value == null
                                && typeof cv?.source_name === "string" && cv.source_name.includes("kein Wert");
                              const cvEmpty = cv && cv.numeric_value == null && cv.text_value == null && !pdfNullSource;
                              const noCv = !cv && !isQualitative;
                              // Hohn-Rendite: wenn auch nur eine Komponente fehlt → wie "Wert fehlt"
                              // behandeln (kein irrefuehrender Partial-Wert).
                              const hohnPartialBlock = fyPartialMissing.length > 0;
                              const isMissing = noCv || pdfNullSource || cvEmpty || hohnPartialBlock || notFound.has(`${company.id}:${d.key}`);
                              if (!isMissing) return null;
                              // Wenn User gerade inline-editing in dieser Cell ist:
                              // missing-Display unterdruecken, damit der edit-input
                              // sichtbar wird (greift NACH dem missing-IIFE).
                              if (isEditing) return null;
                              const reasonMatch = pdfNullSource ? cv?.source_name?.match(/kein Wert:\s*(.+)$/) : null;
                              const pdfReason = reasonMatch?.[1]?.trim();
                              const isCalc = d.source_type === "CALCULATED";
                              // Spezial-Case: pe_ratio bei negativem Net Income ist
                              // mathematisch nicht definiert (Verlust-Jahr). Statt
                              // 'Inputs fehlen' sagen wir das klar.
                              const niForKey = cRows.find((r) => r.value_key === "net_income");
                              const niNum = niForKey?.numeric_value != null ? parseFloat(String(niForKey.numeric_value)) : null;
                              const isPeNotDefined = d.key === "pe_ratio" && niNum != null && niNum <= 0;
                              const tooltipText = isPeNotDefined
                                ? `P/E nicht definiert: Net Income ${niNum != null ? `${(niNum/1_000_000).toFixed(0)} Mio` : ""} ist negativ/null (Verlust-Jahr). KGV ist bei Verlusten konzeptionell nicht aussagekräftig.`
                                : hohnPartialBlock
                                ? `Hohn-Rendite nicht berechenbar — fehlende Komponenten: ${fyPartialMissing.join(", ")}. Klick auf die fehlenden Felder um sie zu fuellen.`
                                : pdfNullSource
                                ? `Annual Report analysiert, kein Wert für diese Kennzahl gefunden${pdfReason ? `: ${pdfReason}` : ""}. Auto-Web-Fallback hat ebenfalls nichts gefunden.`
                                : isCalc
                                ? `Berechnung nicht möglich - benötigte Eingabewerte fehlen${FORMULAS[d.key] ? ` (${FORMULAS[d.key]})` : ""}`
                                : `Wert fehlt - weder im PDF, von Yahoo/EDGAR noch via Web-Recherche gefunden. 'Werte berechnen' nochmal triggern.`;
                              const labelText = isPeNotDefined
                                ? "N/A (Verlust)"
                                : hohnPartialBlock
                                ? "Komponenten fehlen"
                                : pdfNullSource
                                ? "Im Bericht nicht gefunden"
                                : isCalc ? "Inputs fehlen" : "Wert nicht gefunden";
                              // Manueller Override bei leerer Cell: User kann
                              // direkt einen Wert eintragen wenn weder Provider
                              // noch Web/Q-Faktor was gefunden hat.
                              const canManualFill = !isQualitative && d.data_type === "NUMERIC"
                                && period.value === "FY" && period.year != null
                                && !isPeNotDefined;
                              return (
                                <div className="group/nf flex items-center gap-1.5" title={tooltipText}>
                                  <AlertTriangle className="h-3.5 w-3.5 text-amber-600" />
                                  <span className="text-xs text-amber-700">{labelText}</span>
                                  {canManualFill && (
                                    <button
                                      onClick={(e) => {
                                        e.stopPropagation();
                                        setEditCell({ companyId: company.id, key: d.key, value: "" });
                                      }}
                                      className="ml-1 inline-flex items-center rounded border border-amber-400 bg-amber-50 px-1 py-0.5 text-[9px] font-semibold uppercase text-amber-800 hover:bg-amber-100"
                                      title="Wert manuell eintragen (überschreibt den fehlenden Provider-/Web-Wert)"
                                    >
                                      manuell
                                    </button>
                                  )}
                                </div>
                              );
                            })() ?? (
                            <>
                              <span className={`font-mono text-sm ${
                                valuationMode === "adjusted" && modeVal.isFallbackToReported
                                  ? "italic text-slate-500 line-through decoration-slate-400 decoration-1"
                                  : isCalculated
                                    ? "text-foreground"
                                    : "text-blue-700"
                              }`}>
                                {d.data_type === "TEXT"
                                  ? cv?.text_value ?? (cv?.numeric_value != null ? parseFloat(String(cv.numeric_value)).toFixed(2) : t.noValue)
                                  : d.data_type === "FACTOR"
                                  ? cv?.numeric_value != null ? parseFloat(String(cv.numeric_value)).toFixed(2) : (cv?.text_value ?? t.noValue)
                                  : formatValue(displayVal, d.unit, displayCurrency)}
                              </span>
                              {valuationMode === "adjusted" && modeVal.isAdjustedActive && (
                                <span className="shrink-0 rounded bg-amber-200/80 px-1 py-0.5 text-[9px] font-bold uppercase text-amber-900"
                                  title="Adjusted/Non-GAAP-Wert">A</span>
                              )}
                              {valuationMode === "adjusted" && modeVal.isFallbackToReported && (
                                <span className="shrink-0 inline-flex items-center gap-0.5 rounded border border-slate-400 bg-slate-300/60 px-1.5 py-0.5 text-[9px] font-bold uppercase text-slate-700"
                                  title="Adjusted-Wert FEHLT — fallback auf Reported (GAAP/IFRS). Refresh triggert Adjusted-Recherche."
                                >
                                  <AlertTriangle className="h-2.5 w-2.5" />
                                  kein&nbsp;Adj
                                </span>
                              )}
                              {fxUnknown && (
                                <span
                                  title={`Wechselkurs ${cv?.currency} → ${displayCurrency} unbekannt, Wert bleibt in ${cv?.currency}`}
                                  className="shrink-0 rounded bg-amber-100 px-1 py-0.5 text-[10px] font-semibold text-amber-800"
                                >
                                  {cv?.currency}
                                </span>
                              )}
                              {fyIsPartial && (
                                <AlertTriangle className="h-3 w-3 shrink-0 text-amber-600"
                                  aria-label={`Partial: ${fyPartialMissing.join(", ")} fehlen`} />
                              )}
                              {fyAsOfBadge && (
                                <span
                                  title={`Stand FY${cv?.period_year} (${fyAsOfBadge})`}
                                  className="shrink-0 rounded bg-blue-50 px-1 py-0.5 text-[9px] font-medium text-blue-700"
                                >
                                  {fyAsOfBadge}
                                </span>
                              )}
                              {cv?.is_forecast && (
                                <span
                                  title={cv.source_name ? `Schätzung — ${cv.source_name}` : "Schätzung (kein Ist-Wert verfügbar)"}
                                  className="shrink-0 rounded bg-violet-100 px-1 py-0.5 text-[9px] font-bold text-violet-700"
                                >
                                  e
                                </span>
                              )}
                            </>
                            )}
                            {cv && !isQualitative && (
                              <button
                                onClick={(e) => {
                                  e.stopPropagation();
                                  const rect = (e.target as HTMLElement).getBoundingClientRect();
                                  const isOpen = tooltip?.key === d.key && tooltip?.companyId === company.id;
                                  setTooltip(isOpen ? null : { key: d.key, companyId: company.id, x: rect.left, y: rect.bottom + 6 });
                                }}
                                className="shrink-0 rounded p-0.5 text-muted-foreground/50 transition-colors hover:text-muted-foreground">
                                <Info className="h-3 w-3" />
                              </button>
                            )}
                          </div>
                          )}
                        </td>
                      );
                    });
                  })}
                </tr>
                )];
              })}
              {companies.length === 0 && (
                <tr>
                  <td colSpan={visibleDefs.length + grouped.filter((g) => g.defs.length === 0).length + 1}
                    className="px-6 py-12 text-center text-sm text-muted-foreground">
                    Noch keine Firmen in diesem Portfolio.{" "}
                    <Link to={`/portfolios/${pid}/manage`} className="text-primary hover:underline">Firma hinzufügen</Link>
                  </td>
                </tr>
              )}
              {companies.length > 0 && (
                <tr className="border-t border-border/50 bg-muted/20 hover:bg-muted/40">
                  <td colSpan={visibleDefs.length + grouped.filter((g) => g.defs.length === 0).length + 1} className="px-3 py-2">
                    <Link to={`/portfolios/${pid}/manage`}
                      className="inline-flex items-center gap-1.5 text-xs font-medium text-muted-foreground transition-colors hover:text-foreground">
                      <Plus className="h-3.5 w-3.5" />
                      Firma hinzufügen / verwalten
                    </Link>
                  </td>
                </tr>
              )}
            </tbody>
          </table>
        </div>

        {cumDrawer && cumulativeMap.get(cumDrawer.companyId) && (
          <CumulativeBreakdownDrawer
            open={cumDrawerOpen}
            onClose={() => setCumDrawerOpen(false)}
            companyName={cumDrawer.companyName}
            valueKey={cumDrawer.valueKey}
            valueLabel={cumDrawer.valueLabel}
            cum={cumulativeMap.get(cumDrawer.companyId)!}
            onJumpToFy={(year) => {
              const targetIdx = FY_OPTIONS.findIndex((opt) => opt.year === year);
              if (targetIdx >= 0) {
                setPeriodMode("FY");
                setFyIdx(targetIdx);
              }
            }}
          />
        )}

        {tooltip && (() => {
          const cv = getVal(tooltip.companyId, tooltip.key);
          const def = definitions.find((d) => d.key === tooltip.key);
          const tooltipCompany = companies.find((c) => c.id === tooltip.companyId);
          if (!cv || !def) return null;

          const formatAsOf = (): string => {
            if (cv.period_type === "FY" && cv.period_year) {
              const m = tooltipCompany?.fiscal_year_end_month;
              const d = tooltipCompany?.fiscal_year_end_day;
              // Stammdaten (mcap, stock, shares, mcap_calc) = Snapshot am
              // letzten Tag von FY[N-1] (Backtest-Anker). Annual-Werte (NI,
              // FCF, EBITDA …) decken die ganze Periode bis FY-Ende N ab.
              const isStammdatenAnchor = def.category === "STAMMDATEN";
              const yearToShow = isStammdatenAnchor ? cv.period_year - 1 : cv.period_year;
              if (m && d) {
                return `${String(d).padStart(2, "0")}.${String(m).padStart(2, "0")}.${yearToShow} (FY-Ende ${yearToShow})`;
              }
              return `FY ${cv.period_year}`;
            }
            return "aktuell (Snapshot)";
          };

          const proxyAlt = cv.forecast_alternates?.find((a) => a.method === "q_factor_proxy");
          const webAlt = cv.forecast_alternates?.find((a) => a.method === "web_guidance");
          const prevRows = prevYearValuesMap.get(tooltip.companyId) ?? [];
          const prevCv = prevRows.find((r) => r.value_key === tooltip.key);
          const prevNum = prevCv?.numeric_value != null ? (typeof prevCv.numeric_value === "string" ? parseFloat(prevCv.numeric_value) : prevCv.numeric_value) : null;

          // Variant-spezifische Source/Value: bei Q-Faktor-Klick zeigen wir
          // die Q-Faktor-Source aus den alternates, nicht den Primary-Source.
          const isProxyPrimary = (cv.source_name || "").includes("Proxy");
          const isWebPrimary = (cv.source_name || "").includes("Web-Guidance") || (cv.source_name || "").includes("Web-Fallback");
          // displaySource: was im Tooltip "Quelle" gezeigt wird
          let displaySource: string | null = cv.source_name ?? null;
          let displayLink: string | null = cv.source_link ?? null;
          let displayValue: number | null = cv.numeric_value != null ? (typeof cv.numeric_value === "string" ? parseFloat(cv.numeric_value) : cv.numeric_value) : null;
          if (tooltip.variant === "faktor" && !isProxyPrimary && proxyAlt) {
            displaySource = proxyAlt.source ?? null;
            displayLink = null;
            if (proxyAlt.value != null) displayValue = parseFloat(proxyAlt.value);
          } else if (tooltip.variant === "web" && !isWebPrimary && webAlt) {
            displaySource = webAlt.source ?? null;
            displayLink = null;
            if (webAlt.value != null) displayValue = parseFloat(webAlt.value);
          }

          // Confidence-Logic: Variant-aware! Wenn User die Q-Faktor-Variante
          // explizit ansieht (tooltip.variant=='faktor'), zeige Q-Faktor-Badge —
          // selbst wenn primary_method=='web_guidance' (Web ist primaerer Wert).
          // Analog fuer Web-Variante. Im FY-Mode (kein variant) gilt primary_method.
          const pm = cv.primary_method;
          const variantIsFaktor = tooltip.variant === "faktor";
          const variantIsWeb = tooltip.variant === "web";
          const isClaudeResearch = variantIsWeb || (!variantIsFaktor && (
            pm === "web_guidance"
            || (pm == null && ((displaySource || "").includes("Claude-Recherche")
                || (displaySource || "").includes("Web-Guidance")))
          ));
          const isProxySource = variantIsFaktor || (!variantIsWeb && (
            pm === "q_factor_proxy"
            || (pm == null && (displaySource || "").includes("Proxy"))
          ));
          const confidence = (cv.manually_overridden || pm === "manual") && !variantIsFaktor && !variantIsWeb
            ? { label: "Manuell überschrieben", color: "bg-amber-100 text-amber-800 border-amber-300", icon: Pencil }
            : isProxySource
            ? { label: "Q-Faktor-Proxy (Schätzung)", color: "bg-amber-100 text-amber-800 border-amber-300", icon: Calculator }
            : isClaudeResearch
            ? { label: "KI-Recherche", color: "bg-orange-100 text-orange-800 border-orange-300", icon: Sparkles }
            : pm === "pdf" || (pm == null && def.source_type === "API" && (displaySource || "").startsWith("PDF:"))
            ? { label: "Annual Report (PDF)", color: "bg-emerald-100 text-emerald-800 border-emerald-300", icon: ShieldCheck }
            : def.source_type === "API"
            ? { label: "Verifizierte Datenquelle", color: "bg-green-100 text-green-800 border-green-300", icon: ShieldCheck }
            : def.source_type === "CALCULATED"
            ? { label: "Berechnet", color: "bg-blue-100 text-blue-800 border-blue-300", icon: Calculator }
            : def.source_type === "QUALITATIVE"
            ? { label: "Qualitativ (Einschätzung)", color: "bg-amber-100 text-amber-800 border-amber-300", icon: MessageSquare }
            : { label: "Nutzereingabe", color: "bg-slate-100 text-slate-700 border-slate-300", icon: Pencil };

          const ConfIcon = confidence.icon;

          // Stale-Indikator: wenn last_refresh_attempt deutlich neuer als
          // fetched_at, hat der letzte Refresh nichts neues geliefert
          // (Provider/Web fehl, Wert ist veraltet).
          const isStale = (() => {
            if (!cv.last_refresh_attempt || !cv.fetched_at) return false;
            const refresh = new Date(cv.last_refresh_attempt).getTime();
            const fetched = new Date(cv.fetched_at).getTime();
            return refresh - fetched > 60_000; // mehr als 1min Diff
          })();

          return createPortal(
            <>
              <div className="fixed inset-0 z-[99]" onClick={() => setTooltip(null)} />
              <div className="fixed z-[100] flex w-[480px] flex-col overflow-hidden rounded-xl border border-border bg-card shadow-2xl shadow-black/10"
                style={{
                  left: Math.min(tooltip.x, window.innerWidth - 500),
                  // Smart positioning: wenn unten kein Platz, Tooltip nach oben versetzen.
                  top: (() => {
                    const desiredHeight = Math.min(900, window.innerHeight - 32);
                    const margin = 16;
                    if (tooltip.y + desiredHeight + margin <= window.innerHeight) {
                      return tooltip.y;
                    }
                    return Math.max(margin, window.innerHeight - desiredHeight - margin);
                  })(),
                  maxHeight: `${Math.min(900, window.innerHeight - 32)}px`,
                }}>
                <div className="flex flex-col overflow-y-auto">
                {/* Header */}
                <div className="flex items-center justify-between border-b border-border px-5 py-4">
                  <div className="flex flex-col">
                    <span className="text-base font-semibold text-foreground">{def.label_en}</span>
                    <span className="text-xs text-muted-foreground">{def.label_de}</span>
                  </div>
                  <button onClick={() => setTooltip(null)} className="rounded p-1 hover:bg-muted">
                    <X className="h-5 w-5 text-muted-foreground" />
                  </button>
                </div>

                {/* Vertrauenslevel-Badge + optional Stale-Warnung */}
                <div className="px-4 pt-3 space-y-1.5">
                  <div className={`flex items-center gap-2 rounded-lg border px-3 py-1.5 ${confidence.color}`}>
                    <ConfIcon className="h-3.5 w-3.5 shrink-0" />
                    <span className="text-[11px] font-medium">{confidence.label}</span>
                  </div>
                  {isStale && (
                    <div className="flex items-center gap-2 rounded-lg border border-amber-300 bg-amber-50 px-3 py-1.5">
                      <AlertTriangle className="h-3.5 w-3.5 shrink-0 text-amber-700" />
                      <span className="text-[11px] font-medium text-amber-800">
                        Stale: letzter Refresh {cv.last_refresh_attempt ? new Date(cv.last_refresh_attempt).toLocaleString("de-DE") : "—"} lieferte nichts neues. Wert von {cv.fetched_at ? new Date(cv.fetched_at).toLocaleString("de-DE") : "—"}.
                      </span>
                    </div>
                  )}
                </div>

                {/* Section: Formel */}
                {FORMULAS[tooltip.key] && (
                  <section className="mt-3 px-4">
                    <h4 className="mb-1 text-[10px] font-bold uppercase tracking-wider text-blue-700">Formel</h4>
                    <div className="rounded-lg border border-blue-200 bg-blue-50 px-3 py-2">
                      <p className="font-mono text-xs text-blue-900">{FORMULAS[tooltip.key]}</p>
                    </div>
                  </section>
                )}

                {/* Section: Q-Faktor Berechnung */}
                {tooltip.variant === "faktor" && ESTIMATE_PRIMARY_KEYS.has(tooltip.key) && (
                  <section className="mt-4 px-4">
                    <h4 className="mb-2 text-xs font-bold uppercase tracking-wider text-amber-700">Q-Faktor Berechnung</h4>
                    <div className="space-y-3 rounded-lg border-2 border-amber-300 bg-amber-50 px-4 py-3">
                      {prevCv && prevNum != null && (
                        <div className="flex items-baseline justify-between gap-2 border-b border-amber-300 pb-2">
                          <span className="text-xs font-semibold text-amber-700 uppercase tracking-wide">FY{prevCv.period_year}-Basis</span>
                          <span className="font-mono text-sm font-bold text-amber-900">{formatValue(prevNum, def.unit, displayCurrency)}</span>
                        </div>
                      )}
                      {proxyAlt?.explanation ? (
                        <p className="whitespace-pre-wrap text-sm leading-relaxed text-amber-900">{proxyAlt.explanation}</p>
                      ) : proxyAlt?.error_reason ? (
                        <p className="text-sm italic text-amber-800">{proxyAlt.error_reason}</p>
                      ) : (
                        <p className="text-sm italic text-amber-800">Keine Q-Faktor-Erklärung verfügbar — bitte Werte neu berechnen.</p>
                      )}
                    </div>
                  </section>
                )}

                {/* Section: GAAP / Non-GAAP (nur fuer adjusted-relevante Keys) */}
                {ADJUSTABLE_INPUT_KEYS.has(tooltip.key) && (
                  <section className="mt-4 px-4">
                    <h4 className="mb-2 text-xs font-bold uppercase tracking-wider text-indigo-700">
                      Reported vs Adjusted
                    </h4>
                    <div className="rounded-lg border-2 border-indigo-300 bg-indigo-50 px-4 py-3 space-y-2">
                      {(() => {
                        const reportedNum = cv.numeric_value != null
                          ? (typeof cv.numeric_value === "string" ? parseFloat(cv.numeric_value) : cv.numeric_value)
                          : null;
                        const adjustedNum = cv.numeric_value_adjusted != null
                          ? (typeof cv.numeric_value_adjusted === "string" ? parseFloat(cv.numeric_value_adjusted) : cv.numeric_value_adjusted)
                          : null;
                        const diff = (reportedNum != null && adjustedNum != null && reportedNum !== 0)
                          ? ((adjustedNum - reportedNum) / Math.abs(reportedNum)) * 100
                          : null;
                        return (
                          <>
                            <div className="flex items-baseline justify-between gap-2">
                              <span className="text-xs font-semibold text-indigo-700 uppercase tracking-wide">Reported (GAAP/IFRS)</span>
                              <span className="font-mono text-sm font-bold text-indigo-900">
                                {reportedNum != null ? formatValue(reportedNum, def.unit, displayCurrency) : "—"}
                              </span>
                            </div>
                            <div className="flex items-baseline justify-between gap-2 border-t border-indigo-200 pt-2">
                              <span className="text-xs font-semibold text-amber-700 uppercase tracking-wide">Adjusted (Non-GAAP)</span>
                              <span className="font-mono text-sm font-bold text-amber-900">
                                {adjustedNum != null ? formatValue(adjustedNum, def.unit, displayCurrency) : (
                                  <span className="text-xs italic text-muted-foreground">nicht reportet</span>
                                )}
                              </span>
                            </div>
                            {diff != null && (
                              <div className="flex items-baseline justify-between gap-2 border-t border-indigo-200 pt-2">
                                <span className="text-xs text-muted-foreground">Differenz</span>
                                <span className={`font-mono text-sm font-semibold ${diff >= 0 ? "text-emerald-700" : "text-rose-700"}`}>
                                  {diff >= 0 ? "+" : ""}{diff.toFixed(1)}%
                                </span>
                              </div>
                            )}
                            {cv.adjustments_note && (
                              <div className="border-t border-indigo-200 pt-2">
                                <div className="text-[10px] font-semibold uppercase tracking-wide text-muted-foreground">Adjustments</div>
                                <div className="mt-0.5 text-xs text-indigo-900 whitespace-pre-wrap">{cv.adjustments_note}</div>
                              </div>
                            )}
                            {cv.adjustments_source && (
                              <div className="border-t border-indigo-200 pt-2">
                                <div className="text-[10px] font-semibold uppercase tracking-wide text-muted-foreground">Quelle (Adjusted)</div>
                                <div className="mt-0.5 text-[11px] text-indigo-800">{cv.adjustments_source}</div>
                              </div>
                            )}
                          </>
                        );
                      })()}
                    </div>
                  </section>
                )}

                {/* Section: Quelle (variant-spezifisch) */}
                <section className="mt-4 px-4">
                  <h4 className={`mb-2 text-xs font-bold uppercase tracking-wider ${
                    tooltip.variant === "web" ? "text-sky-700" : tooltip.variant === "faktor" ? "text-amber-700" : "text-muted-foreground"
                  }`}>
                    Quelle{tooltip.variant === "faktor" ? " (Q-Faktor)" : tooltip.variant === "web" ? " (Web-Recherche)" : ""}
                  </h4>
                  <div className={`space-y-2 rounded-lg px-4 py-3 ${
                    tooltip.variant === "web"
                      ? "border-2 border-sky-300 bg-sky-50"
                      : tooltip.variant === "faktor"
                        ? "border-2 border-amber-300 bg-amber-50"
                        : "border border-border bg-muted/30"
                  }`}>
                    {displayValue != null && tooltip.variant && (
                      <div className={`flex items-baseline justify-between gap-2 border-b pb-2 ${
                        tooltip.variant === "web" ? "border-sky-300" : "border-amber-300"
                      }`}>
                        <span className={`text-xs font-semibold uppercase tracking-wide ${
                          tooltip.variant === "web" ? "text-sky-700" : "text-amber-700"
                        }`}>
                          {tooltip.variant === "web" ? "Web-Wert" : "Q-Faktor-Wert"}
                        </span>
                        <span className={`font-mono text-sm font-bold ${
                          tooltip.variant === "web" ? "text-sky-900" : "text-amber-900"
                        }`}>{formatValue(displayValue, def.unit, displayCurrency)}</span>
                      </div>
                    )}
                    {(() => {
                      const parsed = cv.is_forecast ? parseQuartalsBreakdown(displaySource) : null;
                      if (!parsed) {
                        return (
                          <div className={`text-sm font-medium ${
                            tooltip.variant === "web" ? "text-sky-900" : tooltip.variant === "faktor" ? "text-amber-900" : "text-foreground"
                          }`}>{displaySource ?? "—"}</div>
                        );
                      }
                      const valCurrency = cv.currency ?? "USD";
                      return (
                        <>
                          <div className={`text-sm font-medium ${
                            tooltip.variant === "web" ? "text-sky-900" : "text-foreground"
                          }`}>{parsed.generalSource}</div>
                          <div className="mt-2 overflow-hidden rounded-md border border-border bg-background/60">
                            <div className="border-b border-border bg-muted/40 px-2 py-1 text-[10px] font-bold uppercase tracking-wider text-muted-foreground">
                              Quartals-Aufschlüsselung
                            </div>
                            <table className="w-full text-[11px] tabular">
                              <tbody>
                                {parsed.quarters.map((qb) => (
                                  <tr key={qb.q} className="border-b border-border/40 last:border-b-0">
                                    <td className="w-12 px-2 py-1 font-semibold text-muted-foreground">Q{qb.q}</td>
                                    <td className="px-2 py-1 font-mono text-foreground">{_formatShortMoney(qb.value, valCurrency)}</td>
                                    <td className="w-16 px-2 py-1 text-right">
                                      <span className={`rounded px-1.5 py-0.5 text-[9px] font-semibold uppercase ${
                                        qb.isActual ? "bg-emerald-100 text-emerald-700" : "bg-sky-100 text-sky-700"
                                      }`}>{qb.isActual ? "Actual" : "Estimate"}</span>
                                    </td>
                                  </tr>
                                ))}
                                {parsed.fyTotal && (
                                  <tr className="bg-muted/30">
                                    <td className="px-2 py-1 font-semibold text-foreground">FY</td>
                                    <td className="px-2 py-1 font-mono font-bold text-foreground">{_formatShortMoney(parsed.fyTotal.value, valCurrency)}</td>
                                    <td className="px-2 py-1 text-right">
                                      <span className="rounded bg-slate-200 px-1.5 py-0.5 text-[9px] font-semibold uppercase text-slate-700">Summe</span>
                                    </td>
                                  </tr>
                                )}
                              </tbody>
                            </table>
                            {parsed.quarters.some((q) => q.context) && (
                              <div className="space-y-0.5 border-t border-border/40 bg-muted/20 px-2 py-1.5 text-[10px] text-muted-foreground">
                                {parsed.quarters.filter((q) => q.context).map((qb) => (
                                  <div key={qb.q}><span className="font-semibold">Q{qb.q}:</span> {qb.context}</div>
                                ))}
                              </div>
                            )}
                          </div>
                        </>
                      );
                    })()}
                    {displayLink && (
                      <a href={displayLink} target="_blank" rel="noreferrer" className={`block truncate text-xs hover:underline ${
                        tooltip.variant === "web" ? "text-sky-700 font-medium" : "text-primary"
                      }`}>
                        {(() => { try { return new URL(displayLink).hostname; } catch { return displayLink; } })()}
                      </a>
                    )}
                  </div>
                </section>

                {/* Section: Metadaten */}
                <section className="mt-3 px-4">
                  <h4 className="mb-1 text-[10px] font-bold uppercase tracking-wider text-muted-foreground">Metadaten</h4>
                  <dl className="grid grid-cols-2 gap-x-3 gap-y-1 rounded-lg border border-border bg-muted/30 px-3 py-2 text-[11px]">
                    <dt className="text-muted-foreground">Wert per</dt>
                    <dd className="text-right font-medium text-foreground">{formatAsOf()}</dd>
                    <dt className="text-muted-foreground">{t.fetchedAt}</dt>
                    <dd className="text-right text-foreground">{cv.fetched_at ? new Date(cv.fetched_at).toLocaleString("de-DE") : "—"}</dd>
                    {cv.currency && (
                      <>
                        <dt className="text-muted-foreground">Originalwährung</dt>
                        <dd className="text-right font-mono text-foreground">{cv.currency}</dd>
                      </>
                    )}
                  </dl>
                </section>

                {/* Section: Aktionen — fuer FY-Werte (Actuals UND Forecasts) */}
                {(() => {
                  const isEditableValue = (
                    cv.period_type === "FY"
                    && cv.period_year != null
                    && def.source_type !== "CALCULATED"
                    && def.source_type !== "QUALITATIVE"
                    && cv.numeric_value != null
                  );
                  if (!isEditableValue) return null;
                  const isForecastValue = cv.is_forecast === true;
                  const tooltipYear = cv.period_year ?? null;
                  const isExplainHere = explainResult?.key === tooltip.key
                    && explainResult?.companyId === tooltip.companyId
                    && explainResult?.periodYear === tooltipYear;
                  const isEditHere = tooltipEdit?.key === tooltip.key
                    && tooltipEdit?.companyId === tooltip.companyId
                    && tooltipEdit?.periodYear === tooltipYear;

                  const handleExplain = async () => {
                    if (explainLoading) return;
                    setExplainLoading(true);
                    setExplainResult(null);
                    try {
                      const res = await explainValue(tooltip.companyId, tooltip.key, "FY", cv.period_year ?? undefined);
                      setExplainResult({
                        key: tooltip.key,
                        companyId: tooltip.companyId,
                        periodYear: tooltipYear,
                        text: res.explanation,
                      });
                    } catch (e) {
                      const detail = (e as { message?: string })?.message;
                      toast.error(detail || "Einordnung fehlgeschlagen");
                    } finally {
                      setExplainLoading(false);
                    }
                  };

                  const handleStartEdit = () => {
                    const cur = cv.numeric_value != null ? String(cv.numeric_value) : "";
                    setTooltipEdit({ key: tooltip.key, companyId: tooltip.companyId, periodYear: tooltipYear, value: cur });
                  };

                  const handleSaveEdit = async () => {
                    if (!tooltipEdit || tooltipEditSaving) return;
                    const num = parseNumericInput(tooltipEdit.value);
                    if (isNaN(num)) {
                      toast.error("Ungueltiger Zahlenwert");
                      return;
                    }
                    setTooltipEditSaving(true);
                    try {
                      await overrideValue(
                        tooltipEdit.companyId, tooltipEdit.key,
                        { numeric_value: num, source_name: "Manuell" },
                        "FY", cv.period_year ?? undefined,
                      );
                      await loadAllValues();
                      toast.success(isForecastValue
                        ? "Forecast-Wert manuell überschrieben (wird beim nächsten 'Werte berechnen' wieder ersetzt)"
                        : "Wert überschrieben (wird beim nächsten 'Werte berechnen' wieder ersetzt)");
                      setTooltipEdit(null);
                      setTooltip(null);
                    } catch (e) {
                      const detail = (e as { message?: string })?.message;
                      toast.error(detail || "Speichern fehlgeschlagen");
                    } finally {
                      setTooltipEditSaving(false);
                    }
                  };

                  return (
                    <section className="mt-3 mb-4 px-4">
                      <h4 className="mb-1 text-[10px] font-bold uppercase tracking-wider text-muted-foreground">Aktionen</h4>
                      <div className="flex gap-2">
                        <button
                          onClick={handleExplain}
                          disabled={explainLoading}
                          className="flex-1 inline-flex items-center justify-center gap-1 rounded-md border border-primary/40 bg-primary/5 px-2 py-1.5 text-[11px] font-medium text-primary transition-colors hover:bg-primary/10 disabled:cursor-not-allowed disabled:opacity-40">
                          {explainLoading
                            ? <><Loader2 className="h-3 w-3 animate-spin" /> Einordnung…</>
                            : <><Sparkles className="h-3 w-3" /> Erklärung</>}
                        </button>
                        <button
                          onClick={handleStartEdit}
                          disabled={isEditHere}
                          className="flex-1 inline-flex items-center justify-center gap-1 rounded-md border border-amber-400/50 bg-amber-50 px-2 py-1.5 text-[11px] font-medium text-amber-800 transition-colors hover:bg-amber-100 disabled:cursor-not-allowed disabled:opacity-40">
                          <Pencil className="h-3 w-3" /> Manuell überschreiben
                        </button>
                      </div>
                      {isEditHere && tooltipEdit && (
                        <div className="mt-2 rounded-lg border border-amber-200 bg-amber-50 p-3">
                          <p className="mb-1 text-[10px] text-amber-700">
                            Achtung: manueller Wert wird beim nächsten "Werte berechnen" wieder überschrieben.
                          </p>
                          <input
                            autoFocus
                            type="text"
                            value={tooltipEdit.value}
                            onChange={(e) => setTooltipEdit({ ...tooltipEdit, value: e.target.value })}
                            onKeyDown={(e) => {
                              if (e.key === "Enter") handleSaveEdit();
                              if (e.key === "Escape") setTooltipEdit(null);
                            }}
                            className="w-full rounded border border-amber-300 bg-white px-2 py-1 font-mono text-sm text-foreground outline-none focus:ring-1 focus:ring-amber-400"
                            placeholder={`Neuer Wert in ${cv.currency || "Base-Units"}`}
                          />
                          <div className="mt-2 flex gap-2">
                            <button
                              onClick={handleSaveEdit}
                              disabled={tooltipEditSaving}
                              className="flex-1 rounded-md bg-amber-600 px-2 py-1 text-[11px] font-medium text-white hover:bg-amber-700 disabled:opacity-40">
                              {tooltipEditSaving ? "Speichert…" : "Speichern"}
                            </button>
                            <button
                              onClick={() => setTooltipEdit(null)}
                              disabled={tooltipEditSaving}
                              className="rounded-md border border-amber-300 bg-white px-2 py-1 text-[11px] font-medium text-amber-800 hover:bg-amber-50">
                              Abbrechen
                            </button>
                          </div>
                        </div>
                      )}
                      {isExplainHere && (
                        <div
                          className="mt-2 rounded-lg border border-primary/30 bg-primary/5 p-3"
                          ref={(el) => {
                            // Auto-scroll zur Erklaerung sobald sie geladen ist,
                            // damit User den Inhalt sieht statt nur den Anfang.
                            if (el) el.scrollIntoView({ behavior: "smooth", block: "nearest" });
                          }}>
                          <div className="mb-1.5 flex items-center justify-between gap-2">
                            <span className="text-[10px] font-bold uppercase tracking-wider text-primary/80">KI-Einordnung</span>
                            <span className="text-[10px] text-muted-foreground italic">scrollbar wenn lang</span>
                          </div>
                          <div
                            className="max-h-[420px] overflow-y-auto pr-1 text-[12px] leading-relaxed text-foreground"
                            dangerouslySetInnerHTML={{ __html: renderMarkdown(explainResult.text) }}
                          />
                        </div>
                      )}
                    </section>
                  );
                })()}
                {/* Bottom-padding fuer scroll-area damit letzte Section
                    nicht direkt am Border klebt */}
                <div className="h-4 shrink-0" />
                </div>
              </div>
            </>,
            document.body
          );
        })()}
      </main>
    </div>
  );
}
