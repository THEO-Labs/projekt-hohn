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
