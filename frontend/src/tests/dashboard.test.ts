import { describe, expect, it } from "vitest";

import { FY_KPI_KEYS, extractFyKpis, sortCompanyCards, type CompanyCardData } from "@/api/dashboard";
import type { CompanyValue } from "@/api/values";
import { formatEarningsDate, isEarningsSoon } from "@/lib/format";

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

// Minimale Karten-Attrappe fuer die Sortier-Tests
const card = (name: string, hReturn: number | string | null): CompanyCardData =>
  ({
    company: { id: name, name },
    h_return_gaap: hReturn == null ? null : { numeric_value: hReturn },
  }) as unknown as CompanyCardData;

describe("sortCompanyCards", () => {
  it("sortiert alphabetisch nach Firmenname aufsteigend", () => {
    const cards = [card("Zeta", 1), card("Alpha", 2), card("Mitte", null)];
    const names = sortCompanyCards(cards, "alphabetical").map((c) => c.company.name);
    expect(names).toEqual(["Alpha", "Mitte", "Zeta"]);
  });

  it("sortiert nach H-Return absteigend, Karten ohne Wert ans Ende", () => {
    const cards = [card("A", 5), card("B", null), card("C", 12), card("D", -3)];
    const names = sortCompanyCards(cards, "h_return").map((c) => c.company.name);
    expect(names).toEqual(["C", "A", "D", "B"]);
  });

  it("parst String-Werte aus dem Wire-Format und bricht Gleichstand per Name", () => {
    const cards = [card("Beta", "7.5"), card("Alpha", 7.5), card("Gamma", "10.0")];
    const names = sortCompanyCards(cards, "h_return").map((c) => c.company.name);
    expect(names).toEqual(["Gamma", "Alpha", "Beta"]);
  });

  it("mutiert das Eingabe-Array nicht", () => {
    const cards = [card("B", 1), card("A", 2)];
    const result = sortCompanyCards(cards, "h_return");
    expect(result).not.toBe(cards);
    expect(cards.map((c) => c.company.name)).toEqual(["B", "A"]);
  });
});

// Karten-Attrappe fuer den earnings-Sortiermodus
const ecard = (name: string, date: string | null): CompanyCardData =>
  ({
    company: { id: name, name, next_earnings_date: date },
    h_return_gaap: null,
  }) as unknown as CompanyCardData;

describe("sortCompanyCards earnings", () => {
  it("sortiert aufsteigend nach next_earnings_date, naechster Termin zuerst", () => {
    const cards = [ecard("A", "2026-09-15"), ecard("B", "2026-08-05"), ecard("C", "2027-01-10")];
    const names = sortCompanyCards(cards, "earnings").map((c) => c.company.name);
    expect(names).toEqual(["B", "A", "C"]);
  });

  it("sortiert Karten ohne Termin ans Ende", () => {
    const cards = [ecard("A", null), ecard("B", "2026-08-05"), ecard("C", null), ecard("D", "2026-08-01")];
    const names = sortCompanyCards(cards, "earnings").map((c) => c.company.name);
    expect(names).toEqual(["D", "B", "A", "C"]);
  });

  it("bricht Gleichstand (gleicher Termin und beide null) per Name", () => {
    const cards = [ecard("Zeta", "2026-08-05"), ecard("Beta", null), ecard("Alpha", "2026-08-05")];
    const names = sortCompanyCards(cards, "earnings").map((c) => c.company.name);
    expect(names).toEqual(["Alpha", "Zeta", "Beta"]);
  });
});

describe("formatEarningsDate", () => {
  const now = new Date(2026, 6, 15); // 15.07.2026

  it("formatiert Termine im laufenden Jahr ohne Jahr", () => {
    expect(formatEarningsDate("2026-08-05", now)).toBe("05.08.");
  });

  it("haengt bei anderem Jahr das Jahr an", () => {
    expect(formatEarningsDate("2027-01-12", now)).toBe("12.01.2027");
  });

  it("liefert leeren String fuer null und unparsebare Werte", () => {
    expect(formatEarningsDate(null, now)).toBe("");
    expect(formatEarningsDate("kaputt", now)).toBe("");
  });
});

describe("isEarningsSoon", () => {
  const now = new Date(2026, 6, 15); // 15.07.2026

  it("markiert heute und genau 7 Tage im Voraus", () => {
    expect(isEarningsSoon("2026-07-15", now)).toBe(true);
    expect(isEarningsSoon("2026-07-22", now)).toBe(true);
  });

  it("markiert 8 Tage im Voraus und Vergangenheit nicht", () => {
    expect(isEarningsSoon("2026-07-23", now)).toBe(false);
    expect(isEarningsSoon("2026-07-14", now)).toBe(false);
  });

  it("liefert false fuer null", () => {
    expect(isEarningsSoon(null, now)).toBe(false);
  });
});

// NaN-Werte (kaputte Strings) muessen wie fehlende ans Ende sortieren.
it("sorts unparseable h-return values to the end", () => {
  const mk = (name: string, hr: unknown) => ({
    company: { name },
    h_return_gaap: hr == null ? null : { numeric_value: hr },
  });
  const cards = [mk("A", "kaputt"), mk("B", "10.0"), mk("C", null)] as never[];
  const sorted = sortCompanyCards(cards, "h_return");
  expect(sorted.map((c: { company: { name: string } }) => c.company.name)).toEqual(["B", "A", "C"]);
});

// Kalender-Range: unmoegliche Daten duerfen weder rendern noch amber triggern.
it("rejects impossible calendar dates", async () => {
  const { formatEarningsDate, isEarningsSoon } = await import("@/lib/format");
  expect(formatEarningsDate("2026-13-40")).toBe("");
  expect(isEarningsSoon("2026-13-40")).toBe(false);
});

it("marks past earnings dates via isEarningsPast", async () => {
  const { isEarningsPast } = await import("@/lib/format");
  expect(isEarningsPast("2020-01-01")).toBe(true);
  expect(isEarningsPast("2099-01-01")).toBe(false);
  expect(isEarningsPast(null)).toBe(false);
});
