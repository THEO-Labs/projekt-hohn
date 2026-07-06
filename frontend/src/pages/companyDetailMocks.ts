// Detail-page section templates. All numeric cells are null; when the backend
// endpoint returns data the adapters below fill the cells. Otherwise cells
// render as "—". No fabricated numbers anywhere.

import { formatNumber, formatPercent } from "@/lib/format";
import type { CompanyDetailOut, QuarterlyRowRefs, ValueRef } from "@/api/detail";

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

const buildQRow = (year: number | null, row: QuarterlyRowRefs, variant: "gaap" | "adj"): QuarterlyRow => ({
  year: year ?? 0,
  q1: refToNum(row.q1, variant),
  q2: refToNum(row.q2, variant),
  q3: refToNum(row.q3, variant),
  q4: refToNum(row.q4, variant),
  annual: refToNum(row.annual, variant),
});

const currencyUnit = (currency: string): string => `${currency} millions`;

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

export function detailToQuarterlySections(detail: CompanyDetailOut): QuarterlySection[] {
  const byKey = new Map(detail.quarterly.map((s) => [s.value_key, s]));
  const revenue = byKey.get("revenue");
  const currency = detail.company.currency;

  return detail.quarterly.map((s) => {
    const showAnnual = s.value_key !== "net_buyback";
    const extras_gaap: ExtraRow[] = [];
    const extras_adj: ExtraRow[] = [];
    const denKey = MARGIN_NUM_BY_DEN[s.value_key];
    if (denKey && revenue) {
      extras_gaap.push(mapMargin(s.current, revenue.current, "gaap"));
      extras_adj.push(mapMargin(s.current, revenue.current, "adj"));
    }
    return {
      title: s.label_en,
      unit: s.is_currency ? currencyUnit(currency) : s.unit ?? undefined,
      showAnnual,
      gaap: {
        rows: [
          buildQRow(s.current_year, s.current, "gaap"),
          buildQRow(s.prior_year, s.prior, "gaap"),
        ],
        extras: extras_gaap.length > 0 ? extras_gaap : undefined,
      },
      adjusted: {
        rows: [
          buildQRow(s.current_year, s.current, "adj"),
          buildQRow(s.prior_year, s.prior, "adj"),
        ],
        extras: extras_adj.length > 0 ? extras_adj : undefined,
      },
    };
  });
}

export function detailToBalanceSheet(detail: CompanyDetailOut): BalanceSheetSection {
  const cy = detail.balance_sheet.current_year ?? new Date().getFullYear();
  const py = detail.balance_sheet.prior_year ?? new Date().getFullYear() - 1;
  const buildGroup = (group: (typeof detail.balance_sheet.groups)[number], variant: "gaap" | "adj"): BSGroup => ({
    rows: group.rows.map((r) => ({
      label: r.label,
      y1: refToNum(r.y1, variant),
      y2: refToNum(r.y2, variant),
      format: r.format_hint === "percent" ? asPct2 : asNum0,
      emphasis: r.emphasis,
    })),
  });
  return {
    title: "Balance Sheet",
    unit: `${detail.company.currency} millions`,
    currentYear: cy,
    priorYear: py,
    gaap: { groups: detail.balance_sheet.groups.map((g) => buildGroup(g, "gaap")) },
    adjusted: { groups: detail.balance_sheet.groups.map((g) => buildGroup(g, "adj")) },
  };
}
