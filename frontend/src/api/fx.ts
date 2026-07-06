import { api } from "./client";

export type FxRatesResponse = {
  base: string;
  date: string | null;
  rates: Record<string, number>;
  source: "frankfurter" | "fallback";
};

export const getFxRates = () => api<FxRatesResponse>("/api/fx/rates");

// Convert value from `source` currency into `target` using USD-based rates
// (rates[X] = X per 1 USD, base is always USD in the backend response).
export function convertCurrency(
  value: number | null,
  source: string,
  target: string,
  rates: Record<string, number>,
): number | null {
  if (value === null || Number.isNaN(value)) return null;
  if (source === target) return value;
  const srcRate = rates[source];
  const tgtRate = rates[target];
  if (!srcRate || !tgtRate) return value;
  return (value / srcRate) * tgtRate;
}

export const DISPLAY_CURRENCIES = [
  "USD", "EUR", "GBP", "CHF", "JPY", "CNY", "HKD", "KRW", "CAD", "AUD",
];
