import type { Company } from "./companies";
import {
  getCompanyValues,
  getValueDefinitions,
  refreshValues,
  type CompanyValue,
  type ValueDefinition,
} from "./values";

export type CompanyCardData = {
  company: Company;
  stock_price: CompanyValue | null;
  shares_outstanding: CompanyValue | null;
  market_cap: CompanyValue | null;
  net_debt: CompanyValue | null;
  enterprise_value: number | null;
  enterprise_value_ts: string | null;
  h_return_gaap: CompanyValue | null;
  h_return_adjusted_value: number | null;
  h_return_adjusted_ts: string | null;
  fy_estimate_year: number | null;
};

const DAILY_KEYS = ["market_cap", "stock_price", "shares_outstanding"];

const findLatest = (rows: CompanyValue[], key: string): CompanyValue | null => {
  const matches = rows.filter((r) => r.value_key === key);
  if (matches.length === 0) return null;
  return matches.reduce((best, r) => {
    const bestYear = best.period_year ?? -Infinity;
    const rYear = r.period_year ?? -Infinity;
    return rYear > bestYear ? r : best;
  });
};

export async function loadCompanyCard(company: Company): Promise<CompanyCardData> {
  const [snapshotRows, fyRows] = await Promise.all([
    getCompanyValues(company.id, "SNAPSHOT").catch(() => [] as CompanyValue[]),
    getCompanyValues(company.id, "FY").catch(() => [] as CompanyValue[]),
  ]);

  const stock_price = findLatest(snapshotRows, "stock_price");
  const shares_outstanding = findLatest(snapshotRows, "shares_outstanding");
  const market_cap = findLatest(snapshotRows, "market_cap");
  const net_debt_snapshot = findLatest(snapshotRows, "net_debt");
  const net_debt_fy = findLatest(fyRows, "net_debt");
  const net_debt = net_debt_snapshot ?? net_debt_fy;

  let enterprise_value: number | null = null;
  let enterprise_value_ts: string | null = null;
  if (market_cap?.numeric_value != null && net_debt?.numeric_value != null) {
    enterprise_value = Number(market_cap.numeric_value) + Number(net_debt.numeric_value);
    const mts = market_cap.fetched_at ? new Date(market_cap.fetched_at).getTime() : 0;
    const dts = net_debt.fetched_at ? new Date(net_debt.fetched_at).getTime() : 0;
    enterprise_value_ts = mts >= dts ? market_cap.fetched_at : net_debt.fetched_at;
  }

  const hReturnRows = fyRows
    .filter((r) => r.value_key === "hohn_return_detailed" && r.period_year != null)
    .sort((a, b) => (b.period_year ?? 0) - (a.period_year ?? 0));
  const h_return_gaap = hReturnRows[0] ?? null;
  const h_return_adjusted_value = h_return_gaap?.numeric_value_adjusted != null
    ? Number(h_return_gaap.numeric_value_adjusted)
    : null;
  const h_return_adjusted_ts = h_return_gaap?.fetched_at ?? null;
  const fy_estimate_year = h_return_gaap?.period_year ?? null;

  return {
    company,
    stock_price,
    shares_outstanding,
    market_cap,
    net_debt,
    enterprise_value,
    enterprise_value_ts,
    h_return_gaap,
    h_return_adjusted_value,
    h_return_adjusted_ts,
    fy_estimate_year,
  };
}

export async function refreshCompanyDaily(companyId: string): Promise<void> {
  await refreshValues(companyId, DAILY_KEYS, "SNAPSHOT", undefined, true);
}

export async function refreshCompanyFull(
  companyId: string,
  fyYear: number,
  definitions: ValueDefinition[],
): Promise<void> {
  const apiKeys = definitions.filter((d) => d.source_type === "API").map((d) => d.key);
  if (apiKeys.length === 0) return;
  await refreshValues(companyId, apiKeys, "FY", fyYear, false);
}

export const getDashboardDefinitions = () => getValueDefinitions();
