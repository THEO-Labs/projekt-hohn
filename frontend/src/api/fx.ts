import { api } from "./client";

export type FxRatesResponse = {
  base: string;
  date: string | null;
  rates: Record<string, number>;
  source: "frankfurter" | "fallback";
};

export const getFxRates = () => api<FxRatesResponse>("/api/fx/rates");
