import { api } from "./client";

export type ValueRef = {
  value: string | null;
  adjusted: string | null;
  source_name: string | null;
  source_link: string | null;
  fetched_at: string | null;
  manually_overridden: boolean;
  primary_method: string | null;
  is_forecast: boolean;
  consistency_flags?: string | null;
  // "not_yet_reported": Periode des laufenden Jahres, deren Zahlen die
  // Firma noch nicht veroeffentlicht haben kann. Grau statt rot rendern.
  status?: string | null;
};

export type QuarterlyRowRefs = {
  q1: ValueRef;
  q2: ValueRef;
  q3: ValueRef;
  q4: ValueRef;
  annual: ValueRef;
};

export type QuarterlySectionOut = {
  value_key: string;
  label_en: string;
  label_de: string;
  is_currency: boolean;
  is_summable: boolean;
  unit: string | null;
  current_year: number | null;
  prior_year: number | null;
  current: QuarterlyRowRefs;
  prior: QuarterlyRowRefs;
};

export type OverviewMetricOut = {
  value_key: string;
  label_en: string;
  label_de: string;
  unit: string | null;
  ref: ValueRef;
};

export type StammdatenPackOut = {
  stock_price: ValueRef;
  shares_outstanding: ValueRef;
  market_cap: ValueRef;
  enterprise_value: ValueRef;
};

export type BalanceSheetRowOut = {
  label: string;
  value_key: string | null;
  y1: ValueRef;
  y2: ValueRef;
  format_hint: "currency" | "percent";
  emphasis: boolean;
};

export type BalanceSheetGroupOut = {
  rows: BalanceSheetRowOut[];
};

export type BalanceSheetSectionOut = {
  current_year: number | null;
  prior_year: number | null;
  groups: BalanceSheetGroupOut[];
};

export type CompanyMetaOut = {
  id: string;
  portfolio_id: string;
  name: string;
  ticker: string;
  isin: string | null;
  currency: string;
  fiscal_year_end_month: number | null;
  fiscal_year_end_day: number | null;
  // Serverseitig aus der ISIN abgeleitet: US-Filer -> "US-GAAP", sonst "IFRS"
  accounting_standard: "IFRS" | "US-GAAP";
};

export type CompanyDetailOut = {
  company: CompanyMetaOut;
  current_fy_estimate_year: number | null;
  prior_fy_year: number | null;
  stammdaten: StammdatenPackOut;
  overview_metrics: OverviewMetricOut[];
  quarterly: QuarterlySectionOut[];
  balance_sheet: BalanceSheetSectionOut;
};

export const getCompanyDetail = (companyId: string) =>
  api<CompanyDetailOut>(`/api/companies/${companyId}/detail`);
