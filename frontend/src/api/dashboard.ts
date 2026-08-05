import type { Company } from "./companies";
import {
  getCompanyValues,
  getValueDefinitions,
  refreshValues,
  type CompanyValue,
  type ValueDefinition,
} from "./values";

// KPI-Bestandteile des H-Returns, Anzeige-Reihenfolge auf der Karte
export const FY_KPI_KEYS = [
  "dividend_yield",
  "ni_growth",
  "net_buyback_yield",
  "net_debt_change_pct",
  "pe_ratio",
  "h_peg",
  "ps_ratio",
  "fcf_yield",
  "net_debt_to_ocf",
] as const;

export type FyKpiKey = (typeof FY_KPI_KEYS)[number];

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
  fy_kpis: Record<FyKpiKey, number | null>;
};

const DAILY_KEYS = ["market_cap", "stock_price", "shares_outstanding"];

export type CardSortMode = "alphabetical" | "h_return" | "earnings";

// Sortiert die Karten fuer die Anzeige — rein und nicht-destruktiv (neues Array).
// "alphabetical": Firmenname aufsteigend (de-Locale).
// "h_return": H-Return IFRS (hohn_return_detailed, numeric_value) absteigend,
// Karten ohne Wert ans Ende; Gleichstand faellt auf den Namen zurueck.
// "earnings": next_earnings_date aufsteigend (naechster Termin zuerst),
// Karten ohne Termin ans Ende; Gleichstand faellt auf den Namen zurueck.
export function sortCompanyCards(
  cards: CompanyCardData[],
  mode: CardSortMode,
): CompanyCardData[] {
  const byName = (a: CompanyCardData, b: CompanyCardData) =>
    a.company.name.localeCompare(b.company.name, "de");
  const sorted = [...cards];
  if (mode === "h_return") {
    // API liefert Decimal ggf. als String; nicht-parsebare Werte (NaN)
    // wie fehlende behandeln, sonst bricht die Comparator-Transitivitaet.
    const hReturn = (c: CompanyCardData): number | null => {
      if (c.h_return_gaap?.numeric_value == null) return null;
      const n = Number(c.h_return_gaap.numeric_value);
      return Number.isFinite(n) ? n : null;
    };
    sorted.sort((a, b) => {
      const av = hReturn(a);
      const bv = hReturn(b);
      if (av == null && bv == null) return byName(a, b);
      if (av == null) return 1;
      if (bv == null) return -1;
      if (bv !== av) return bv - av;
      return byName(a, b);
    });
  } else if (mode === "earnings") {
    // ISO-Daten (YYYY-MM-DD) sortieren lexikographisch korrekt chronologisch.
    sorted.sort((a, b) => {
      const av = a.company.next_earnings_date ?? null;
      const bv = b.company.next_earnings_date ?? null;
      if (av == null && bv == null) return byName(a, b);
      if (av == null) return 1;
      if (bv == null) return -1;
      if (av !== bv) return av < bv ? -1 : 1;
      return byName(a, b);
    });
  } else {
    sorted.sort(byName);
  }
  return sorted;
}

// Bei Actual+Forecast-Paaren fuer dieselbe Zelle gewinnt IMMER die
// Actual-Zeile (is_forecast=false) — das Backend liefert ohne ORDER BY,
// sonst kann die Karte nichtdeterministisch einen stale Forecast zeigen.
const pickRow = (candidates: CompanyValue[]): CompanyValue | null => {
  if (candidates.length === 0) return null;
  return candidates.find((r) => !r.is_forecast) ?? candidates[0];
};

export const findLatest = (rows: CompanyValue[], key: string): CompanyValue | null => {
  const matches = rows.filter((r) => r.value_key === key);
  if (matches.length === 0) return null;
  const withYear = matches.filter((r) => r.period_year != null);
  if (withYear.length === 0) {
    // SNAPSHOT-Rows tragen kein period_year — juengste nach fetched_at.
    const sorted = [...matches].sort(
      (a, b) =>
        (b.fetched_at ? Date.parse(b.fetched_at) : 0) -
        (a.fetched_at ? Date.parse(a.fetched_at) : 0),
    );
    return pickRow(sorted);
  }
  const maxYear = Math.max(...withYear.map((r) => r.period_year as number));
  return pickRow(withYear.filter((r) => r.period_year === maxYear));
};

// Zieht die KPI-Werte aus den FY-Rows. Bevorzugt das Jahr der H-Return-Zeile
// (Konsistenz mit dem FY-Badge), sonst das jeweils juengste vorhandene Jahr.
export function extractFyKpis(
  fyRows: CompanyValue[],
  fyYear: number | null,
): Record<FyKpiKey, number | null> {
  const kpis = {} as Record<FyKpiKey, number | null>;
  for (const key of FY_KPI_KEYS) {
    const row = fyYear != null
      ? pickRow(fyRows.filter((r) => r.value_key === key && r.period_year === fyYear))
      : findLatest(fyRows, key);
    kpis[key] = row?.numeric_value != null ? Number(row.numeric_value) : null;
  }
  return kpis;
}

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
  const fy_kpis = extractFyKpis(fyRows, fy_estimate_year);

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
    fy_kpis,
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
