// Detail-page section templates. All cells are empty (value=null); when the
// backend endpoint returns data the adapters below fill the cells with values
// AND their provenance (source, primary_method, fetched_at) so the UI can
// render color-coding and click-to-inspect popovers.

import { formatNumber, formatPercent } from "@/lib/format";
import type { CompanyDetailOut, QuarterlyRowRefs, ValueRef } from "@/api/detail";
import { convertCurrency } from "@/api/fx";

export type FxContext = {
  displayCurrency: string;
  rates: Record<string, number> | null;
};

// --- Cell abstraction ------------------------------------------------------
// Each rendered number carries its value plus provenance so the popover can
// show source_name/link/fetched_at/method + a formula (for computed cells).

export type Cell = {
  value: number | null;
  source_name?: string | null;
  source_link?: string | null;
  fetched_at?: string | null;
  primary_method?: string | null;
  manually_overridden?: boolean;
  is_forecast?: boolean;
  formula?: string | null;
  value_key?: string | null;
  period_label?: string | null;
  // Marks an adjusted-column cell that fell back to the GAAP value because
  // no separate Non-GAAP figure was reported. Rendered in muted colour with
  // "≈" prefix to signal "same as GAAP".
  is_gaap_fallback?: boolean;
};

export const emptyCell = (): Cell => ({ value: null });

export type QuarterlyRow = {
  year: number;
  q1: Cell;
  q2: Cell;
  q3: Cell;
  q4: Cell;
  annual: Cell;
};

export type ExtraRow = {
  label: string;
  q1: Cell;
  q2: Cell;
  q3: Cell;
  q4: Cell;
  annual: Cell;
  format?: (v: number | null) => string;
};

export type QuarterlyBlockData = {
  rows: QuarterlyRow[];
  extras?: ExtraRow[];
};

export type QuarterlySection = {
  title: string;
  unit?: string;
  showAnnual?: boolean;
  gaap: QuarterlyBlockData;
  adjusted: QuarterlyBlockData;
};

const asPct = (v: number | null) => formatPercent(v, 1);
const asPct2 = (v: number | null) => formatPercent(v, 2);
const asNum0 = (v: number | null) => formatNumber(v, 0);

const emptyRow = (year: number): QuarterlyRow => ({
  year,
  q1: emptyCell(),
  q2: emptyCell(),
  q3: emptyCell(),
  q4: emptyCell(),
  annual: emptyCell(),
});

const emptyBlock: QuarterlyBlockData = {
  rows: [emptyRow(new Date().getFullYear()), emptyRow(new Date().getFullYear() - 1)],
};

const emptySection = (
  title: string,
  opts: { unit?: string; showAnnual?: boolean } = {},
): QuarterlySection => ({
  title,
  unit: opts.unit,
  showAnnual: opts.showAnnual,
  gaap: emptyBlock,
  adjusted: emptyBlock,
});

export const QUARTERLY_SECTIONS: QuarterlySection[] = [
  emptySection("Revenue"),
  emptySection("Net Income"),
  emptySection("EPS (Diluted)"),
  emptySection("Operating Cash Flow"),
  emptySection("CapEx"),
  emptySection("Free Cash Flow"),
  emptySection("Dividends"),
  emptySection("Buybacks", { showAnnual: false }),
  emptySection("SBC"),
  emptySection("Net Buyback"),
];

// --- Balance sheet ---------------------------------------------------------

export type BSRow = {
  label: string;
  y1: Cell;
  y2: Cell;
  format?: (v: number | null) => string;
  emphasis?: boolean;
};
export type BSGroup = { rows: BSRow[] };
export type BSData = { groups: BSGroup[] };

export type BalanceSheetSection = {
  title: string;
  unit?: string;
  currentYear: number;
  priorYear: number;
  gaap: BSData;
  adjusted: BSData;
};

const emptyBs: BSData = {
  groups: [
    {
      rows: [
        { label: "Cash", y1: emptyCell(), y2: emptyCell(), format: asNum0 },
        { label: "Short-Term Investments", y1: emptyCell(), y2: emptyCell(), format: asNum0 },
        { label: "Total Cash", y1: emptyCell(), y2: emptyCell(), format: asNum0, emphasis: true },
      ],
    },
    {
      rows: [
        { label: "ST Debt", y1: emptyCell(), y2: emptyCell(), format: asNum0 },
        { label: "LT Debt", y1: emptyCell(), y2: emptyCell(), format: asNum0 },
        { label: "Sum Debt", y1: emptyCell(), y2: emptyCell(), format: asNum0, emphasis: true },
      ],
    },
    {
      rows: [
        { label: "Net Debt", y1: emptyCell(), y2: emptyCell(), format: asNum0, emphasis: true },
        { label: "Change", y1: emptyCell(), y2: emptyCell(), format: asNum0 },
        { label: "in % of Market Cap", y1: emptyCell(), y2: emptyCell(), format: asPct2 },
      ],
    },
  ],
};

export const BALANCE_SHEET: BalanceSheetSection = {
  title: "Balance Sheet",
  currentYear: new Date().getFullYear(),
  priorYear: new Date().getFullYear() - 1,
  gaap: emptyBs,
  adjusted: emptyBs,
};

// --- Formula catalog (for the popover) ------------------------------------

export const FORMULAS: Record<string, string> = {
  // Overview / valuation metrics
  hohn_return_detailed: "Dividend Yield + NI Growth + Net Buyback Yield + ΔNet Debt Yield",
  hohn_return_simple: "FCF Yield + NI Growth − SBC Yield + ΔNet Debt Yield",
  h_peg: "PE Ratio / H-Return Detailed",
  dividend_yield: "Dividends / Market Cap × 100",
  ni_growth: "(NI[Y] - NI[Y-1]) / |NI[Y-1]| × 100",
  net_buyback: "Buyback Volume - SBC",
  net_buyback_yield: "Net Buyback / Market Cap × 100",
  buyback_yield: "Buyback Volume / Market Cap × 100",
  sbc_yield: "SBC / Market Cap × 100",
  net_debt_change: "Net Debt[Y-1] - Net Debt[Y]",
  net_debt_change_pct: "ΔNet Debt / Market Cap × 100",
  fcf_yield: "FCF / Market Cap × 100",
  pe_ratio: "Market Cap / Net Income",
  ev_ebitda: "(Market Cap + Net Debt) / EBITDA",
  ps_ratio: "Market Cap / Revenue",
  ni_margin: "Net Income / Revenue × 100",
  ocf_margin: "Operating Cash Flow / Revenue × 100",
  fcf_margin: "Free Cash Flow / Revenue × 100",
  market_cap_calc: "Stock Price × Shares Outstanding",
  actual_return: "(MCap end / MCap start - 1) × 100",
};

// --- Adapter helpers -------------------------------------------------------

const toNum = (v: string | null | undefined): number | null => {
  if (v == null || v === "") return null;
  const n = Number(v);
  return Number.isFinite(n) ? n : null;
};

const refValue = (r: ValueRef | undefined, variant: "gaap" | "adj"): number | null =>
  toNum(variant === "gaap" ? r?.value ?? null : r?.adjusted ?? null);

// For the adjusted column: if the company doesn't report a separate Non-GAAP
// figure, just copy the GAAP value silently (no "≈" marker, same color).
// Rationale: Excel-style — Non-GAAP = GAAP when no adjustments exist.
const refValueWithFallback = (
  r: ValueRef | undefined,
  variant: "gaap" | "adj",
): { value: number | null; is_gaap_fallback: boolean } => {
  if (variant === "gaap") return { value: refValue(r, "gaap"), is_gaap_fallback: false };
  const adj = refValue(r, "adj");
  if (adj !== null) return { value: adj, is_gaap_fallback: false };
  return { value: refValue(r, "gaap"), is_gaap_fallback: false };
};

function refToCell(
  r: ValueRef | undefined,
  variant: "gaap" | "adj",
  scaleFactor: number,
  fx: FxContext,
  nativeCurrency: string,
  isCurrency: boolean,
  valueKey: string,
  periodLabel: string,
): Cell {
  const { value: raw, is_gaap_fallback } = refValueWithFallback(r, variant);
  let value = raw;
  if (value !== null) value = value / scaleFactor;
  if (isCurrency && value !== null && fx.rates && nativeCurrency !== fx.displayCurrency) {
    value = convertCurrency(value, nativeCurrency, fx.displayCurrency, fx.rates);
  }
  return {
    value,
    source_name: r?.source_name ?? null,
    source_link: r?.source_link ?? null,
    fetched_at: r?.fetched_at ?? null,
    primary_method: r?.primary_method ?? null,
    manually_overridden: r?.manually_overridden ?? false,
    is_forecast: r?.is_forecast ?? false,
    formula: FORMULAS[valueKey] ?? null,
    value_key: valueKey,
    period_label: periodLabel,
    is_gaap_fallback,
  };
}

const buildQRow = (
  year: number | null,
  row: QuarterlyRowRefs,
  variant: "gaap" | "adj",
  factor: number,
  fx: FxContext,
  nativeCurrency: string,
  isCurrency: boolean,
  valueKey: string,
): QuarterlyRow => {
  const y = year ?? 0;
  const suffix = (q: string) => `${q} ${y}`;
  return {
    year: y,
    q1: refToCell(row.q1, variant, factor, fx, nativeCurrency, isCurrency, valueKey, suffix("Q1")),
    q2: refToCell(row.q2, variant, factor, fx, nativeCurrency, isCurrency, valueKey, suffix("Q2")),
    q3: refToCell(row.q3, variant, factor, fx, nativeCurrency, isCurrency, valueKey, suffix("Q3")),
    q4: refToCell(row.q4, variant, factor, fx, nativeCurrency, isCurrency, valueKey, suffix("Q4")),
    annual: refToCell(row.annual, variant, factor, fx, nativeCurrency, isCurrency, valueKey, `FY ${y}`),
  };
};

const currencyUnit = (currency: string): string => `${currency} millions`;

const RAW_CURRENCY_KEYS = new Set<string>(["eps_diluted"]);
const scaleFor = (s: { value_key: string; is_currency: boolean }): number =>
  s.is_currency && !RAW_CURRENCY_KEYS.has(s.value_key) ? 1_000_000 : 1;

// Frontend-side computed extras: Change % and Margin. These carry no source
// but do carry a formula description.
const computedCell = (value: number | null, formula: string, periodLabel: string): Cell => ({
  value,
  formula,
  primary_method: "calculated",
  period_label: periodLabel,
});

const MARGIN_NUM_BY_DEN: Record<string, string> = {
  net_income: "revenue",
  operating_cash_flow: "revenue",
  fcf: "revenue",
};

function mapMargin(
  section: { current: QuarterlyRowRefs; label_en: string; value_key: string; current_year: number | null },
  denominatorSection: { current: QuarterlyRowRefs },
  variant: "gaap" | "adj",
): ExtraRow {
  const ratio = (num: number | null, den: number | null): number | null => {
    if (num === null || den === null || den === 0) return null;
    return (num / den) * 100;
  };
  const num = section.current;
  const den = denominatorSection.current;
  const numVal = (q: keyof QuarterlyRowRefs) => refValue(num[q], variant);
  const denVal = (q: keyof QuarterlyRowRefs) => refValue(den[q], variant);
  const y = section.current_year ?? 0;
  const label = `${section.label_en} Margin`;
  const formula = `${section.label_en} / Revenue × 100`;
  return {
    label: "Margin",
    q1: computedCell(ratio(numVal("q1"), denVal("q1")), formula, `${label} Q1 ${y}`),
    q2: computedCell(ratio(numVal("q2"), denVal("q2")), formula, `${label} Q2 ${y}`),
    q3: computedCell(ratio(numVal("q3"), denVal("q3")), formula, `${label} Q3 ${y}`),
    q4: computedCell(ratio(numVal("q4"), denVal("q4")), formula, `${label} Q4 ${y}`),
    annual: computedCell(ratio(numVal("annual"), denVal("annual")), formula, `${label} FY ${y}`),
    format: asPct,
  };
}

export function detailToQuarterlySections(detail: CompanyDetailOut, fx: FxContext): QuarterlySection[] {
  const byKey = new Map(detail.quarterly.map((s) => [s.value_key, s]));
  const revenue = byKey.get("revenue");
  const nativeCurrency = detail.company.currency;

  return detail.quarterly.map((s) => {
    const showAnnual = s.value_key !== "net_buyback";
    const factor = scaleFor(s);
    const extras_gaap: ExtraRow[] = [];
    const extras_adj: ExtraRow[] = [];
    const denKey = MARGIN_NUM_BY_DEN[s.value_key];
    if (denKey && revenue) {
      extras_gaap.push(mapMargin(s, revenue, "gaap"));
      extras_adj.push(mapMargin(s, revenue, "adj"));
    }
    const unit = !s.is_currency
      ? s.unit ?? undefined
      : RAW_CURRENCY_KEYS.has(s.value_key)
        ? fx.displayCurrency
        : currencyUnit(fx.displayCurrency);

    return {
      title: s.label_en,
      unit,
      showAnnual,
      gaap: {
        rows: [
          buildQRow(s.current_year, s.current, "gaap", factor, fx, nativeCurrency, s.is_currency, s.value_key),
          buildQRow(s.prior_year, s.prior, "gaap", factor, fx, nativeCurrency, s.is_currency, s.value_key),
        ],
        extras: extras_gaap.length > 0 ? extras_gaap : undefined,
      },
      adjusted: {
        rows: [
          buildQRow(s.current_year, s.current, "adj", factor, fx, nativeCurrency, s.is_currency, s.value_key),
          buildQRow(s.prior_year, s.prior, "adj", factor, fx, nativeCurrency, s.is_currency, s.value_key),
        ],
        extras: extras_adj.length > 0 ? extras_adj : undefined,
      },
    };
  });
}

// Balance sheet: y1/y2 as Cells with source info.
export function detailToBalanceSheet(detail: CompanyDetailOut, fx: FxContext): BalanceSheetSection {
  const cy = detail.balance_sheet.current_year ?? new Date().getFullYear();
  const py = detail.balance_sheet.prior_year ?? new Date().getFullYear() - 1;
  const nativeCurrency = detail.company.currency;

  const bsCell = (
    ref: ValueRef,
    variant: "gaap" | "adj",
    isPct: boolean,
    label: string,
    year: number,
    keyHint: string | null,
  ): Cell => {
    const { value: raw, is_gaap_fallback } = refValueWithFallback(ref, variant);
    let value = raw;
    if (value !== null && !isPct) value = value / 1_000_000;
    if (!isPct && value !== null && fx.rates && nativeCurrency !== fx.displayCurrency) {
      value = convertCurrency(value, nativeCurrency, fx.displayCurrency, fx.rates);
    }
    return {
      value,
      source_name: ref.source_name ?? null,
      source_link: ref.source_link ?? null,
      fetched_at: ref.fetched_at ?? null,
      primary_method: ref.primary_method ?? null,
      manually_overridden: ref.manually_overridden ?? false,
      is_forecast: ref.is_forecast ?? false,
      formula: keyHint ? FORMULAS[keyHint] ?? null : null,
      value_key: keyHint ?? null,
      period_label: `${label} FY ${year}`,
      is_gaap_fallback,
    };
  };

  const buildGroup = (
    group: (typeof detail.balance_sheet.groups)[number],
    variant: "gaap" | "adj",
  ): BSGroup => ({
    rows: group.rows.map((r) => {
      const isPct = r.format_hint === "percent";
      return {
        label: r.label,
        y1: bsCell(r.y1, variant, isPct, r.label, cy, r.value_key),
        y2: bsCell(r.y2, variant, isPct, r.label, py, r.value_key),
        format: isPct ? asPct2 : asNum0,
        emphasis: r.emphasis,
      };
    }),
  });

  return {
    title: "Balance Sheet",
    unit: `${fx.displayCurrency} millions`,
    currentYear: cy,
    priorYear: py,
    gaap: { groups: detail.balance_sheet.groups.map((g) => buildGroup(g, "gaap")) },
    adjusted: { groups: detail.balance_sheet.groups.map((g) => buildGroup(g, "adj")) },
  };
}
