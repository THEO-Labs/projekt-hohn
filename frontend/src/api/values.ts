import { api } from "./client";

export type ValueDefinition = {
  key: string;
  label_de: string;
  label_en: string;
  category: string;
  source_type: string;
  data_type: string;
  unit: string | null;
  sort_order: number;
  always_current: boolean;
  is_currency: boolean;
};

export type ForecastAlternate = {
  method: "web_guidance" | "q_factor_proxy" | string;
  value: string | null;
  currency: string | null;
  source: string | null;
  explanation?: string;
  error_reason?: string;
  fallback_from?: string;
};

export type CompanyValue = {
  id: string;
  company_id: string;
  value_key: string;
  period_year: number | null;
  period_type: string;
  is_forecast: boolean;
  numeric_value: number | null;
  text_value: string | null;
  currency: string | null;
  source_name: string | null;
  source_link: string | null;
  fetched_at: string | null;
  manually_overridden: boolean;
  forecast_alternates: ForecastAlternate[] | null;
};

export const getValueDefinitions = () =>
  api<ValueDefinition[]>("/api/value-definitions");

export const getCompanyValues = (
  companyId: string,
  periodType?: string,
  periodYear?: number
) => {
  const params = new URLSearchParams();
  if (periodType) params.set("period_type", periodType);
  if (periodYear) params.set("period_year", String(periodYear));
  return api<CompanyValue[]>(`/api/companies/${companyId}/values?${params}`);
};

export const refreshValues = (
  companyId: string,
  keys: string[],
  periodType = "SNAPSHOT",
  periodYear?: number
) =>
  api<CompanyValue[]>(`/api/companies/${companyId}/values/refresh`, {
    method: "POST",
    body: JSON.stringify({
      keys,
      period_type: periodType,
      period_year: periodYear ?? null,
    }),
  });

export type RefreshStatus = {
  company_id: string;
  total: number;
  completed: number;
  successful?: number;
  current_key: string | null;
  status: "running" | "done" | "failed" | "idle";
  phase?: "fetching" | "prev_year_inputs" | "calculating";
  phase_label?: string | null;
  started_at?: string;
  finished_at?: string | null;
};

export const getRefreshStatus = (companyId: string) =>
  api<RefreshStatus>(`/api/companies/${companyId}/refresh-status`);

export const calculateValues = (
  companyId: string,
  periodType?: string,
  periodYear?: number,
) => {
  const params = new URLSearchParams();
  if (periodType) params.set("period_type", periodType);
  if (periodYear != null) params.set("period_year", String(periodYear));
  return api<CompanyValue[]>(`/api/companies/${companyId}/values/calculate?${params}`, { method: "POST" });
};

export type FyAvailability = {
  fy_years_with_data: number[];
  keys_per_year: Record<string, string[]>;
  has_snapshot_market_cap: boolean;
  is_us: boolean;
  annual_report_years: number[];
  quarter_years: number[];
};

export const getFyAvailability = (companyId: string) =>
  api<FyAvailability>(`/api/companies/${companyId}/fy-availability`);

export type CumulativeCell = {
  cum: string | null;
  pa_avg: string | null;
  pa_cagr: string | null;
  missing: string[];
};

export type CumulativeValuesResponse = {
  from_year: number;
  to_year: number;
  n_years: number;
  market_cap: string | null;
  market_cap_anchor?: string;
  snapshot_market_cap?: string | null;
  values: Record<string, CumulativeCell>;
  per_year_breakdown: Record<string, Record<string, string | null>>;
  pre_period_year: number;
  pre_period_breakdown: Record<string, string | null>;
  missing_years: number[];
};

export const getCumulativeValues = (
  companyId: string,
  fromYear: number,
  toYear: number,
) => {
  const params = new URLSearchParams();
  params.set("from_year", String(fromYear));
  params.set("to_year", String(toYear));
  return api<CumulativeValuesResponse>(`/api/companies/${companyId}/values/cumulative?${params}`);
};

export const fetchHistoricalStammdaten = (companyId: string, periodYear: number) =>
  api<{ stored: boolean; reason?: string; period_year?: number }>(
    `/api/companies/${companyId}/values/historical-stammdaten?period_year=${periodYear}`,
    { method: "POST" }
  );

export type ResearchResult = {
  value: string | null;
  source_name: string | null;
  source_url: string | null;
  value_found: boolean;
};

export const researchValue = (
  companyId: string,
  valueKey: string,
  periodType: string = "FY",
  periodYear?: number,
) => {
  const params = new URLSearchParams();
  params.set("period_type", periodType);
  if (periodYear) params.set("period_year", String(periodYear));
  return api<ResearchResult>(`/api/companies/${companyId}/values/${valueKey}/research?${params}`, {
    method: "POST",
  });
};

export const overrideValue = (
  companyId: string,
  valueKey: string,
  payload: { numeric_value?: number; text_value?: string; source_name: string },
  periodType: string = "SNAPSHOT",
  periodYear?: number,
) => {
  const params = new URLSearchParams();
  params.set("period_type", periodType);
  if (periodYear) params.set("period_year", String(periodYear));
  return api<CompanyValue>(`/api/companies/${companyId}/values/${valueKey}/override?${params}`, {
    method: "POST",
    body: JSON.stringify(payload),
  });
};
