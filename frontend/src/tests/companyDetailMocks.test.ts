import { describe, expect, it } from "vitest";

import { detailToQuarterlySections, type FxContext } from "@/pages/companyDetailMocks";
import type { CompanyDetailOut, QuarterlyRowRefs, ValueRef } from "@/api/detail";

// Minimaler Baukasten fuer Detail-Responses
const ref = (value: string | null = null, adjusted: string | null = null): ValueRef => ({
  value,
  adjusted,
  source_name: null,
  source_link: null,
  fetched_at: null,
  manually_overridden: false,
  primary_method: value !== null ? "calculated" : null,
  is_forecast: false,
});

const rowRefs = (annualValue: string | null, annualAdjusted: string | null = null): QuarterlyRowRefs => ({
  q1: ref(),
  q2: ref(),
  q3: ref(),
  q4: ref(),
  annual: ref(annualValue, annualAdjusted),
});

const section = (value_key: string, label: string, annualValue: string | null, annualAdjusted: string | null = null) => ({
  value_key,
  label_en: label,
  label_de: label,
  is_currency: true,
  is_summable: true,
  unit: null,
  current_year: 2026,
  prior_year: 2025,
  current: rowRefs(annualValue, annualAdjusted),
  prior: rowRefs(null),
});

const detail = (): CompanyDetailOut => ({
  company: {
    id: "c1",
    portfolio_id: "p1",
    name: "TestCo",
    ticker: "TST",
    isin: "US0001234567",
    currency: "USD",
    fiscal_year_end_month: 12,
    fiscal_year_end_day: 31,
    accounting_standard: "US-GAAP",
  },
  current_fy_estimate_year: 2026,
  prior_fy_year: 2025,
  stammdaten: {
    stock_price: ref(),
    shares_outstanding: ref(),
    market_cap: ref(),
    enterprise_value: ref(),
  },
  overview_metrics: [],
  quarterly: [
    section("revenue", "Revenue", "1000000000"),
    section("net_buyback", "Net Buyback", "250000000", "300000000"),
  ],
  balance_sheet: { current_year: 2026, prior_year: 2025, groups: [] },
});

const fx: FxContext = { displayCurrency: "USD", rates: null };

// Sektion mit expliziten Q-Werten (GAAP, adjusted) fuer Margen-Tests.
const qSection = (
  value_key: string,
  label: string,
  quarters: Record<"q1" | "q2" | "q3" | "q4", [string | null, string | null]>,
) => ({
  value_key,
  label_en: label,
  label_de: label,
  is_currency: true,
  is_summable: true,
  unit: null,
  current_year: 2026,
  prior_year: 2025,
  current: {
    q1: ref(...quarters.q1),
    q2: ref(...quarters.q2),
    q3: ref(...quarters.q3),
    q4: ref(...quarters.q4),
    annual: ref(),
  },
  prior: rowRefs(null),
});

describe("detailToQuarterlySections", () => {
  it("zeigt die Jahresspalte auch fuer net_buyback (FY-Zelle GAAP + adjusted)", () => {
    const sections = detailToQuarterlySections(detail(), fx);
    const nb = sections.find((s) => s.title === "Net Buyback");
    expect(nb).toBeDefined();
    expect(nb!.showAnnual).toBe(true);
    // Werte in Mio skaliert (is_currency, kein RAW-Key)
    expect(nb!.gaap.rows[0].annual.value).toBe(250);
    expect(nb!.adjusted.rows[0].annual.value).toBe(300);
  });

  it("laesst die Jahresspalte der uebrigen Sektionen an", () => {
    const sections = detailToQuarterlySections(detail(), fx);
    const rev = sections.find((s) => s.title === "Revenue");
    expect(rev!.showAnnual).toBe(true);
    expect(rev!.gaap.rows[0].annual.value).toBe(1000);
  });

  it("Adjusted-Marge: GAAP-Fallback je Zelle statt leerem Strich", () => {
    // Visa-Muster: NI adjusted ueberall, Revenue adjusted nur Q4,
    // OCF hat nie ein Adjusted.
    const d = detail();
    d.quarterly = [
      qSection("revenue", "Revenue", {
        q1: ["1000", null],
        q2: ["1000", null],
        q3: ["1000", null],
        q4: ["1000", "1100"],
      }),
      qSection("net_income", "Net Income", {
        q1: ["200", "220"],
        q2: ["200", "220"],
        q3: ["200", "220"],
        q4: ["200", "220"],
      }),
      qSection("operating_cash_flow", "Operating Cash Flow", {
        q1: ["300", null],
        q2: ["300", null],
        q3: ["300", null],
        q4: ["300", null],
      }),
    ];
    const sections = detailToQuarterlySections(d, fx);

    // NI-Marge adjusted: echtes Adjusted-NI / GAAP-Revenue-Fallback = 22%.
    const ni = sections.find((s) => s.title === "Net Income")!;
    const niAdjMargin = ni.adjusted.extras![0];
    expect(niAdjMargin.q1.value).toBe(22);
    expect(niAdjMargin.q1.is_gaap_fallback).toBeFalsy();
    // Q4 hat echtes Adjusted-Revenue: 220 / 1100 = 20%.
    expect(niAdjMargin.q4.value).toBe(20);

    // OCF-Marge adjusted: kein Adjusted-OCF -> GAAP-Wert als markierter
    // Fallback (30%), kein leerer Strich.
    const ocf = sections.find((s) => s.title === "Operating Cash Flow")!;
    const ocfAdjMargin = ocf.adjusted.extras![0];
    expect(ocfAdjMargin.q1.value).toBe(30);
    expect(ocfAdjMargin.q1.is_gaap_fallback).toBe(true);

    // GAAP-Ansicht unveraendert.
    const ocfGaapMargin = ocf.gaap.extras![0];
    expect(ocfGaapMargin.q1.value).toBe(30);
    expect(ocfGaapMargin.q1.is_gaap_fallback).toBeFalsy();
  });
});
