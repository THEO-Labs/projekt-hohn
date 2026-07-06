// Detail-page section templates. All numeric cells are null; when the backend
// endpoint returns data the adapters below fill the cells. Otherwise cells
// render as "—". No fabricated numbers anywhere.

import { formatNumber, formatPercent } from "@/lib/format";
import type { CompanyDetailOut, QuarterlyRowRefs, ValueRef } from "@/api/detail";
import { convertCurrency } from "@/api/fx";

export type FxContext = {
  displayCurrency: string;
  rates: Record<string, number> | null;
};

export type QuarterlyRow = {
  year: number;
  q1: number | null;
  q2: number | null;
  q3: number | null;
  q4: number | null;
  annual: number | null;
};

export type ExtraRow = {
  label: string;
  q1: number | null;
  q2: number | null;
  q3: number | null;
  q4: number | null;
  annual: number | null;
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
  q1: null, q2: null, q3: null, q4: null, annual: null,
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

// Section titles + display order for the detail page. No numeric data — cells
// render "—" until the backend supplies real values.
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

// -- Balance sheet ---------------------------------------------------------

export type BSRow = {
  label: string;
  y1: number | null;
  y2: number | null;
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
        { label: "Cash", y1: null, y2: null, format: asNum0 },
        { label: "Short-Term Investments", y1: null, y2: null, format: asNum0 },
        { label: "Total Cash", y1: null, y2: null, format: asNum0, emphasis: true },
      ],
    },
    {
      rows: [
        { label: "ST Debt", y1: null, y2: null, format: asNum0 },
        { label: "LT Debt", y1: null, y2: null, format: asNum0 },
        { label: "Sum Debt", y1: null, y2: null, format: asNum0, emphasis: true },
      ],
    },
    {
      rows: [
        { label: "Net Debt", y1: null, y2: null, format: asNum0, emphasis: true },
        { label: "Change", y1: null, y2: null, format: asNum0 },
        { label: "in % of Market Cap", y1: null, y2: null, format: asPct2 },
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

// -- Adapters from backend response ---------------------------------------

const toNum = (v: string | null | undefined): number | null => {
  if (v == null || v === "") return null;
  const n = Number(v);
  return Number.isFinite(n) ? n : null;
};

const refToNum = (r: ValueRef | undefined, variant: "gaap" | "adj"): number | null =>
  toNum(variant === "gaap" ? r?.value ?? null : r?.adjusted ?? null);

const scaled = (v: number | null, factor: number): number | null =>
  v === null ? null : v / factor;

const buildQRow = (
  year: number | null,
  row: QuarterlyRowRefs,
  variant: "gaap" | "adj",
  factor: number,
): QuarterlyRow => ({
  year: year ?? 0,
  q1: scaled(refToNum(row.q1, variant), factor),
  q2: scaled(refToNum(row.q2, variant), factor),
  q3: scaled(refToNum(row.q3, variant), factor),
  q4: scaled(refToNum(row.q4, variant), factor),
  annual: scaled(refToNum(row.annual, variant), factor),
});

const currencyUnit = (currency: string): string => `${currency} millions`;

// EPS is monetary but per-share, not in millions.
const RAW_CURRENCY_KEYS = new Set<string>(["eps_diluted"]);
const scaleFor = (s: { value_key: string; is_currency: boolean }): number =>
  s.is_currency && !RAW_CURRENCY_KEYS.has(s.value_key) ? 1_000_000 : 1;

const mapMargin = (
  numerator: QuarterlyRowRefs,
  denominator: QuarterlyRowRefs,
  variant: "gaap" | "adj",
): ExtraRow => {
  const ratio = (num: number | null, den: number | null): number | null => {
    if (num == null || den == null || den === 0) return null;
    return (num / den) * 100;
  };
  return {
    label: "Margin",
    q1: ratio(refToNum(numerator.q1, variant), refToNum(denominator.q1, variant)),
    q2: ratio(refToNum(numerator.q2, variant), refToNum(denominator.q2, variant)),
    q3: ratio(refToNum(numerator.q3, variant), refToNum(denominator.q3, variant)),
    q4: ratio(refToNum(numerator.q4, variant), refToNum(denominator.q4, variant)),
    annual: ratio(refToNum(numerator.annual, variant), refToNum(denominator.annual, variant)),
    format: asPct,
  };
};

const MARGIN_NUM_BY_DEN: Record<string, string> = {
  net_income: "revenue",
  operating_cash_flow: "revenue",
  fcf: "revenue",
};

const convertQRow = (row: QuarterlyRow, source: string, fx: FxContext, isCurrency: boolean): QuarterlyRow => {
  if (!isCurrency || !fx.rates || source === fx.displayCurrency) return row;
  const conv = (v: number | null) => convertCurrency(v, source, fx.displayCurrency, fx.rates!);
  return {
    year: row.year,
    q1: conv(row.q1),
    q2: conv(row.q2),
    q3: conv(row.q3),
    q4: conv(row.q4),
    annual: conv(row.annual),
  };
};

export function detailToQuarterlySections(detail: CompanyDetailOut, fx: FxContext): QuarterlySection[] {
  const byKey = new Map(detail.quarterly.map((s) => [s.value_key, s]));
  const revenue = byKey.get("revenue");
  const nativeCurrency = detail.company.currency;
  const displayCurrency = fx.displayCurrency;

  return detail.quarterly.map((s) => {
    const showAnnual = s.value_key !== "net_buyback";
    const factor = scaleFor(s);
    const extras_gaap: ExtraRow[] = [];
    const extras_adj: ExtraRow[] = [];
    const denKey = MARGIN_NUM_BY_DEN[s.value_key];
    if (denKey && revenue) {
      // Margin math is a ratio — scale + FX cancels out.
      extras_gaap.push(mapMargin(s.current, revenue.current, "gaap"));
      extras_adj.push(mapMargin(s.current, revenue.current, "adj"));
    }
    const unit = !s.is_currency
      ? s.unit ?? undefined
      : RAW_CURRENCY_KEYS.has(s.value_key)
        ? displayCurrency
        : currencyUnit(displayCurrency);

    const isCurrency = s.is_currency;
    return {
      title: s.label_en,
      unit,
      showAnnual,
      gaap: {
        rows: [
          convertQRow(buildQRow(s.current_year, s.current, "gaap", factor), nativeCurrency, fx, isCurrency),
          convertQRow(buildQRow(s.prior_year, s.prior, "gaap", factor), nativeCurrency, fx, isCurrency),
        ],
        extras: extras_gaap.length > 0 ? extras_gaap : undefined,
      },
      adjusted: {
        rows: [
          convertQRow(buildQRow(s.current_year, s.current, "adj", factor), nativeCurrency, fx, isCurrency),
          convertQRow(buildQRow(s.prior_year, s.prior, "adj", factor), nativeCurrency, fx, isCurrency),
        ],
        extras: extras_adj.length > 0 ? extras_adj : undefined,
      },
    };
  });
}

export function detailToBalanceSheet(detail: CompanyDetailOut, fx: FxContext): BalanceSheetSection {
  const cy = detail.balance_sheet.current_year ?? new Date().getFullYear();
  const py = detail.balance_sheet.prior_year ?? new Date().getFullYear() - 1;
  const nativeCurrency = detail.company.currency;
  const buildGroup = (group: (typeof detail.balance_sheet.groups)[number], variant: "gaap" | "adj"): BSGroup => ({
    rows: group.rows.map((r) => {
      const isPct = r.format_hint === "percent";
      const factor = isPct ? 1 : 1_000_000;
      let y1 = scaled(refToNum(r.y1, variant), factor);
      let y2 = scaled(refToNum(r.y2, variant), factor);
      if (!isPct && fx.rates && nativeCurrency !== fx.displayCurrency) {
        y1 = convertCurrency(y1, nativeCurrency, fx.displayCurrency, fx.rates);
        y2 = convertCurrency(y2, nativeCurrency, fx.displayCurrency, fx.rates);
      }
      return {
        label: r.label,
        y1,
        y2,
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
