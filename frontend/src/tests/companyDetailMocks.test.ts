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
});
