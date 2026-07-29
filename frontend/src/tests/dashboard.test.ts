import { describe, expect, it } from "vitest";

import { FY_KPI_KEYS, extractFyKpis } from "@/api/dashboard";
import type { CompanyValue } from "@/api/values";

// Minimaler FY-Row-Baukasten fuer die Tests
const row = (value_key: string, period_year: number, numeric_value: number | null): CompanyValue => ({
  id: `${value_key}-${period_year}`,
  company_id: "c1",
  value_key,
  period_year,
  period_type: "FY",
  is_forecast: false,
  numeric_value,
  text_value: null,
  currency: null,
  source_name: null,
  source_link: null,
  fetched_at: null,
  manually_overridden: false,
  forecast_alternates: null,
});

describe("extractFyKpis", () => {
  it("nimmt bei gesetztem FY-Jahr nur Werte dieses Jahres", () => {
    const rows = [
      row("pe_ratio", 2025, 22.5),
      row("pe_ratio", 2024, 18.1),
      row("dividend_yield", 2024, 3.2), // falsches Jahr -> null
    ];
    const kpis = extractFyKpis(rows, 2025);
    expect(kpis.pe_ratio).toBe(22.5);
    expect(kpis.dividend_yield).toBeNull();
  });

  it("faellt ohne FY-Jahr auf das juengste vorhandene Jahr zurueck", () => {
    const rows = [row("fcf_yield", 2023, 4.0), row("fcf_yield", 2024, 5.5)];
    const kpis = extractFyKpis(rows, null);
    expect(kpis.fcf_yield).toBe(5.5);
  });

  it("liefert null fuer fehlende Keys und alle 9 Keys sind vorhanden", () => {
    const kpis = extractFyKpis([], 2025);
    expect(Object.keys(kpis)).toHaveLength(FY_KPI_KEYS.length);
    for (const key of FY_KPI_KEYS) expect(kpis[key]).toBeNull();
  });
});

// Kritischer Review-Befund: Actual+Forecast-Paar -> Actual gewinnt immer.
it("prefers the actual row over a stale forecast duplicate", () => {
  const rows = [
    { value_key: "pe_ratio", period_year: 2026, numeric_value: 99, is_forecast: true },
    { value_key: "pe_ratio", period_year: 2026, numeric_value: 19.1, is_forecast: false },
  ] as never[];
  const kpis = extractFyKpis(rows, 2026);
  expect(kpis.pe_ratio).toBe(19.1);
});

// API liefert Decimal als String — Number()-Parse muss das abfangen.
it("parses string numeric values from the wire format", () => {
  const rows = [
    { value_key: "dividend_yield", period_year: 2026, numeric_value: "2.030000", is_forecast: false },
  ] as never[];
  const kpis = extractFyKpis(rows, 2026);
  expect(kpis.dividend_yield).toBeCloseTo(2.03);
});
