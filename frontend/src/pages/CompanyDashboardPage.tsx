import { useEffect, useState, useCallback } from "react";
import { Link, useParams } from "react-router-dom";
import { ChevronLeft, ChevronRight, ChevronDown, RefreshCw, Info, X, Plus, ShieldCheck, Calculator, MessageSquare, Pencil, Sparkles, AlertTriangle, Loader2, Lock, Search } from "lucide-react";
import { toast } from "sonner";
import { createPortal } from "react-dom";
import { AppHeader } from "@/components/AppHeader";
import { useAuth } from "@/hooks/useAuth";
import { t } from "@/lib/i18n";
import { formatValue } from "@/lib/format";
import { listCompanies, type Company } from "@/api/companies";
import {
  getValueDefinitions,
  getCompanyValues,
  getCumulativeValues,
  getFyAvailability,
  getRefreshStatus,
  refreshValues,
  overrideValue,
  researchValue,
  fetchHistoricalStammdaten,
  type ValueDefinition,
  type CompanyValue,
  type CumulativeValuesResponse,
  type FyAvailability,
  type RefreshStatus,
} from "@/api/values";
import { AnalysisDrawer } from "@/components/AnalysisDrawer";
import { CumulativeBreakdownDrawer } from "@/components/CumulativeBreakdownDrawer";
import { RefreshProgressBar } from "@/components/RefreshProgressBar";
import { getFxRates } from "@/api/fx";
import { parseNumericInput } from "@/lib/parseNumeric";

const CATEGORY_ORDER = [
  "HOHN_RETURN", "FCF", "NI_GROWTH", "SBC", "BUYBACKS",
  "DIVIDENDS", "DEBT", "STAMMDATEN",
];

// Faktoren die in der Hohn-Formel auftauchen — die zeigen wir in der
// kompakten Ansicht. buyback_yield (brutto) ist NICHT in der Formel,
// nur net_buyback_yield (= Buybacks − SBC) — daher hier raus.
const FACTOR_KEYS = new Set([
  "hohn_return_simple",
  "hohn_return_detailed",
  "actual_return",
  "fcf_yield",
  "ni_growth",
  "sbc_yield",
  "net_buyback_yield",
  "dividend_yield",
  "net_debt_change_pct",
  "market_cap",
]);

const PREV_YEAR_DISPLAY_KEYS = new Set([
  "net_income",
  "net_debt",
]);

const CUM_INPUT_FY_KEYS = [
  "fcf",
  "net_income",
  "sbc",
  "buyback_volume",
  "dividends",
  "net_debt",
];


const CATEGORY_LABELS: Record<string, string> = {
  HOHN_RETURN: "Hohn-Rendite",
  FCF: "FCF Yield",
  NI_GROWTH: "Net Income Growth",
  SBC: "SBC",
  BUYBACKS: "Buybacks",
  DIVIDENDS: "Dividend Yield",
  DEBT: "Net Debt",
  STAMMDATEN: "Stammdaten",
};

const CATEGORY_COLORS: Record<string, string> = {
  STAMMDATEN: "bg-slate-100 text-slate-700 border-slate-200",
  DEBT: "bg-rose-50 text-rose-700 border-rose-200",
  SBC: "bg-amber-50 text-amber-700 border-amber-200",
  BUYBACKS: "bg-orange-50 text-orange-700 border-orange-200",
  FCF: "bg-teal-50 text-teal-700 border-teal-200",
  NI_GROWTH: "bg-violet-50 text-violet-700 border-violet-200",
  DIVIDENDS: "bg-emerald-50 text-emerald-700 border-emerald-200",
  HOHN_RETURN: "bg-sky-100 text-sky-800 border-sky-300",
};

type PeriodOption =
  | { label: string; value: "FY"; year: number; from_year?: undefined; to_year?: undefined }
  | { label: string; value: "CUM"; year?: undefined; from_year: number; to_year: number };

const FY_OPTIONS: { label: string; year: number }[] = [
  { label: "FY 2026e", year: 2026 },
  { label: "FY 2025", year: 2025 },
  { label: "FY 2024", year: 2024 },
  { label: "FY 2023", year: 2023 },
  { label: "FY 2022", year: 2022 },
  { label: "FY 2021", year: 2021 },
  { label: "FY 2020", year: 2020 },
];

const CUM_TO_YEAR = 2025;
const CUM_FROM_YEARS = [2024, 2023, 2022, 2021, 2020];
const CUM_OPTIONS: { label: string; from_year: number; to_year: number }[] = CUM_FROM_YEARS.map((fy) => ({
  label: `seit ${fy}`,
  from_year: fy,
  to_year: CUM_TO_YEAR,
}));

const FALLBACK_FX_RATES: Record<string, number> = {
  USD: 1, EUR: 0.92, GBP: 0.79, CHF: 0.88, JPY: 155, KRW: 1390,
  HKD: 7.8, CNY: 7.2, CAD: 1.35, AUD: 1.52, SEK: 10.5, NOK: 10.5,
  DKK: 6.9, SGD: 1.34, INR: 83, BRL: 5.0, MXN: 17, ZAR: 18.5,
};
const CURRENCIES = ["USD", "EUR", "GBP", "CHF", "JPY", "KRW", "CNY", "HKD"];

const FORMULAS: Record<string, string> = {
  market_cap_calc: "Stock Price × Shares Outstanding",
  net_buyback: "Buyback-Volumen − SBC",
  sbc_yield: "SBC / Market Cap × 100",
  buyback_yield: "Buyback Volume / Market Cap × 100",
  net_buyback_yield: "Net Buyback / Market Cap × 100",
  fcf_yield: "FCF / Market Cap × 100",
  ni_growth: "(NI[Y] − NI[Y−1]) / |NI[Y−1]| × 100",
  net_debt_change: "Net Debt[Y−1] − Net Debt[Y]",
  net_debt_change_pct: "ΔNet Debt / Market Cap × 100",
  dividend_yield: "Dividends / Market Cap × 100",
  hohn_return_simple: "FCF Yield + NI Growth − SBC/MCap + ΔND/MCap",
  hohn_return_detailed: "Dividend Yield + NI Growth + Net Buyback/MCap + ΔND/MCap",
  actual_return: "(MCap Ende FY / MCap Anfang FY − 1) × 100  [TSR via Yahoo Adj Close]",
};

type ColorTier = "excellent" | "good" | "neutral" | "weak" | "bad";

const HIGHER_BETTER_THRESHOLDS: Record<string, [number, number, number, number]> = {
  hohn_return_simple: [15, 10, 5, 0],
  hohn_return_detailed: [15, 10, 5, 0],
  fcf_yield: [6, 4, 2, 0],
  ni_growth: [15, 8, 0, -5],
  buyback_yield: [4, 2, 0.5, 0],
  net_buyback_yield: [3, 1.5, 0, -1],
  dividend_yield: [3, 1.5, 0.5, 0],
  net_debt_change_pct: [3, 0.5, -1, -3],
};

const LOWER_BETTER_THRESHOLDS: Record<string, [number, number, number, number]> = {
  sbc_yield: [0.5, 1, 2, 4],
};

function colorTier(key: string, value: number | null): ColorTier | null {
  if (value == null || isNaN(value)) return null;
  const hb = HIGHER_BETTER_THRESHOLDS[key];
  if (hb) {
    const [exc, good, weak, bad] = hb;
    if (value >= exc) return "excellent";
    if (value >= good) return "good";
    if (value >= weak) return "neutral";
    if (value >= bad) return "weak";
    return "bad";
  }
  const lb = LOWER_BETTER_THRESHOLDS[key];
  if (lb) {
    const [exc, good, weak, bad] = lb;
    if (value <= exc) return "excellent";
    if (value <= good) return "good";
    if (value <= weak) return "neutral";
    if (value <= bad) return "weak";
    return "bad";
  }
  return null;
}

const TIER_BG: Record<ColorTier, string> = {
  excellent: "bg-emerald-100/70",
  good: "bg-green-50",
  neutral: "",
  weak: "bg-orange-50",
  bad: "bg-red-100/60",
};

const HOHN_LOCKED_KEYS = new Set(["hohn_return_simple", "hohn_return_detailed"]);

function isHohnLocked(av: FyAvailability | undefined, periodYear: number | undefined): boolean {
  if (!av || periodYear === undefined) return false;
  if (periodYear >= new Date().getFullYear()) return false;
  if (av.is_us) return false;
  return !av.annual_report_years.includes(periodYear);
}

type TooltipState = { key: string; companyId: string; x: number; y: number } | null;

export function CompanyDashboardPage() {
  const { pid } = useParams<{ pid: string }>();
  const { user, logout } = useAuth();

  const [definitions, setDefinitions] = useState<ValueDefinition[]>([]);
  const [companies, setCompanies] = useState<Company[]>([]);
  const [valuesMap, setValuesMap] = useState<Map<string, CompanyValue[]>>(new Map());
  const [periodMode, setPeriodMode] = useState<"FY" | "CUM">("FY");
  const [fyIdx, setFyIdx] = useState(0);
  const [cumIdx, setCumIdx] = useState(0);
  const [displayCurrency, setDisplayCurrency] = useState("USD");
  const [isLoadingPeriod, setIsLoadingPeriod] = useState(false);
  const [expandedSections, setExpandedSections] = useState<Set<string>>(new Set());
  const [tooltip, setTooltip] = useState<TooltipState>(null);
  const [editCell, setEditCell] = useState<{ companyId: string; key: string; value: string } | null>(null);
  const [saving, setSaving] = useState(false);
  const [notFound, setNotFound] = useState<Set<string>>(new Set());
  const [drawer, setDrawer] = useState<{
    companyId: string;
    valueKey: string;
    companyName: string;
    valueLabel: string;
    currentScore: number | null;
    currentText: string | undefined;
    isQualitative: boolean;
    isAlwaysCurrent: boolean;
    isCalculated: boolean;
    dataType: string;
  } | null>(null);
  const [drawerOpen, setDrawerOpen] = useState(false);
  const [cumDrawer, setCumDrawer] = useState<{ companyId: string; companyName: string; valueKey: string; valueLabel: string } | null>(null);
  const [cumDrawerOpen, setCumDrawerOpen] = useState(false);
  const [prevYearValuesMap, setPrevYearValuesMap] = useState<Map<string, CompanyValue[]>>(new Map());
  const [cumulativeMap, setCumulativeMap] = useState<Map<string, CumulativeValuesResponse>>(new Map());
  const [availabilityMap, setAvailabilityMap] = useState<Map<string, FyAvailability>>(new Map());
  const [refreshStatuses, setRefreshStatuses] = useState<Map<string, RefreshStatus>>(new Map());
  const [fxRates, setFxRates] = useState<Record<string, number>>(FALLBACK_FX_RATES);
  const [researching, setResearching] = useState<string | null>(null);

  const period: PeriodOption = periodMode === "FY"
    ? { label: FY_OPTIONS[fyIdx].label, value: "FY", year: FY_OPTIONS[fyIdx].year }
    : { label: CUM_OPTIONS[cumIdx].label, value: "CUM", from_year: CUM_OPTIONS[cumIdx].from_year, to_year: CUM_OPTIONS[cumIdx].to_year };

  const loadAllValues = useCallback(async () => {
    if (!pid || companies.length === 0) return;
    setIsLoadingPeriod(true);
    try {
    const availMap = new Map<string, FyAvailability>();
    await Promise.all(
      companies.map(async (c) => {
        try {
          availMap.set(c.id, await getFyAvailability(c.id));
        } catch {
          availMap.set(c.id, { fy_years_with_data: [], keys_per_year: {}, has_snapshot_market_cap: false, is_us: false, annual_report_years: [] });
        }
      })
    );
    setAvailabilityMap(availMap);

    const qualitativeOnlyKeys = new Set(
      definitions.filter((d) => d.source_type === "QUALITATIVE").map((d) => d.key)
    );
    const alwaysCurrentApiKeys = new Set(
      definitions.filter((d) => d.always_current && d.source_type !== "QUALITATIVE").map((d) => d.key)
    );

    if (period.value === "CUM") {
      const cumMap = new Map<string, CumulativeValuesResponse>();
      const snapMap = new Map<string, CompanyValue[]>();
      await Promise.all(
        companies.map(async (c) => {
          try {
            const cum = await getCumulativeValues(c.id, period.from_year, period.to_year);
            cumMap.set(c.id, cum);
          } catch {
            // skip
          }
          try {
            const snap = await getCompanyValues(c.id, "SNAPSHOT");
            snapMap.set(c.id, snap);
          } catch {
            // skip
          }
        })
      );
      setCumulativeMap(cumMap);
      setValuesMap(snapMap);
      setPrevYearValuesMap(new Map());
      return;
    }

    const map = new Map<string, CompanyValue[]>();
    const prevMap = new Map<string, CompanyValue[]>();
    const wantsPrev = period.value === "FY" && period.year !== undefined && period.year > 0;
    const isHistoricalFy = period.value === "FY" && period.year !== undefined && period.year < new Date().getFullYear();
    await Promise.all(
      companies.map(async (c) => {
        if (isHistoricalFy && period.year !== undefined) {
          try { await fetchHistoricalStammdaten(c.id, period.year); } catch {}
          if (!c.fiscal_year_end_month || !c.fiscal_year_end_day) {
            // FY-end may have been auto-detected on backend → refresh company state
            try {
              const fresh = await listCompanies(pid);
              setCompanies(fresh);
            } catch {}
          }
        }
        const periodVals = await getCompanyValues(c.id, period.value, period.year);
        const snapshotVals = await getCompanyValues(c.id, "SNAPSHOT");
        const periodKeyMap = new Map(periodVals.map((v) => [v.value_key, v]));
        const allKeys = new Set([...periodVals.map((v) => v.value_key), ...snapshotVals.map((v) => v.value_key)]);
        const merged = [...allKeys].map((key) => {
          if (qualitativeOnlyKeys.has(key)) {
            return snapshotVals.find((v) => v.value_key === key) ?? periodKeyMap.get(key);
          }
          if (alwaysCurrentApiKeys.has(key)) {
            return periodKeyMap.get(key) ?? snapshotVals.find((v) => v.value_key === key);
          }
          return periodKeyMap.get(key) ?? snapshotVals.find((v) => v.value_key === key);
        }).filter(Boolean) as CompanyValue[];
        map.set(c.id, merged);
        if (wantsPrev) {
          try {
            const prevVals = await getCompanyValues(c.id, "FY", (period.year as number) - 1);
            prevMap.set(c.id, prevVals);
          } catch {
            prevMap.set(c.id, []);
          }
        }
      })
    );
    setValuesMap(map);
    setPrevYearValuesMap(prevMap);
    } finally {
      setIsLoadingPeriod(false);
    }
  }, [pid, companies, period.value, period.year, period.from_year, period.to_year, definitions]);

  const pollStatuses = useCallback(async (companyList: Company[]) => {
    if (companyList.length === 0) return;
    const entries = await Promise.all(
      companyList.map(async (c) => {
        try {
          const s = await getRefreshStatus(c.id);
          return [c.id, s] as [string, RefreshStatus];
        } catch {
          return [c.id, { status: "idle" } as RefreshStatus] as [string, RefreshStatus];
        }
      })
    );
    setRefreshStatuses((prev) => {
      const next = new Map(prev);
      for (const [id, newStatus] of entries) {
        const prevStatus = prev.get(id);
        if (newStatus.status === "idle" && prevStatus?.status === "running") {
          continue;
        }
        next.set(id, newStatus);
      }
      return next;
    });
  }, []);

  useEffect(() => {
    getValueDefinitions().then(setDefinitions);
    getFxRates()
      .then((r) => setFxRates(r.rates))
      .catch(() => undefined);
  }, []);

  useEffect(() => {
    if (pid) listCompanies(pid).then((list) => {
      setCompanies(list);
      pollStatuses(list);
    });
  }, [pid, pollStatuses]);

  const reloadAvailability = useCallback(async () => {
    if (companies.length === 0) {
      setAvailabilityMap(new Map());
      return;
    }
    const map = new Map<string, FyAvailability>();
    await Promise.all(
      companies.map(async (c) => {
        try {
          map.set(c.id, await getFyAvailability(c.id));
        } catch {
          map.set(c.id, { fy_years_with_data: [], keys_per_year: {}, has_snapshot_market_cap: false, is_us: false, annual_report_years: [] });
        }
      })
    );
    setAvailabilityMap(map);
  }, [companies]);

  useEffect(() => {
    reloadAvailability();
  }, [reloadAvailability]);

  useEffect(() => {
    setNotFound(new Set());
    loadAllValues();
  }, [loadAllValues]);

  useEffect(() => {
    const anyRunning = Array.from(refreshStatuses.values()).some((s) => s.status === "running");
    if (!anyRunning) return;
    const timer = setInterval(() => pollStatuses(companies), 2000);
    return () => clearInterval(timer);
  }, [refreshStatuses, companies, pollStatuses]);

  const toggleCategory = (cat: string) => {
    setExpandedSections((prev) => {
      const next = new Set(prev);
      next.has(cat) ? next.delete(cat) : next.add(cat);
      return next;
    });
  };

  const handleRefreshCompany = async (c: Company) => {
    const apiKeys = definitions.filter((d) => d.source_type === "API").map((d) => d.key);
    setRefreshStatuses((prev) => new Map(prev).set(c.id, { company_id: c.id, total: apiKeys.length, completed: 0, current_key: null, status: "running" as const }));
    try {
      const updated = await refreshValues(c.id, apiKeys, period.value, period.year);
      setValuesMap((prev) => {
        const next = new Map(prev);
        const existing = next.get(c.id) ?? [];
        const merged = new Map(existing.map((v) => [`${v.value_key}:${v.period_type}:${v.period_year}`, v]));
        for (const u of updated) merged.set(`${u.value_key}:${u.period_type}:${u.period_year}`, u);
        next.set(c.id, Array.from(merged.values()));
        return next;
      });
      const returnedKeys = new Set(updated.map((u) => u.value_key));
      setNotFound((prev) => {
        const next = new Set(prev);
        for (const k of apiKeys) {
          const nfKey = `${c.id}:${k}`;
          returnedKeys.has(k) ? next.delete(nfKey) : next.add(nfKey);
        }
        return next;
      });
    } catch (err) {
      console.error(`Refresh failed for ${c.name}:`, err);
    } finally {
      await pollStatuses(companies);
      await loadAllValues();
    }
  };

  const handleResearch = async (
    companyId: string,
    companyName: string,
    key: string,
    valueLabel: string,
    dataType: string,
  ) => {
    const researchKey = `${companyId}:${key}`;
    if (researching) return;
    setResearching(researchKey);
    try {
      const res = await researchValue(companyId, key, period.value, period.year);
      const score = res.message.score_suggestion;
      const numericScore = score == null ? null : (typeof score === "string" ? parseFloat(score) : score);
      if (!res.value_found) {
        toast.warning("Claude konnte keinen verifizierbaren Wert finden — siehe Chat für Details");
      }
      setDrawer({
        companyId,
        valueKey: key,
        companyName,
        valueLabel,
        currentScore: numericScore,
        currentText: undefined,
        isQualitative: false,
        isAlwaysCurrent: key === "stock_price" || key === "shares_outstanding" || key === "market_cap",
        isCalculated: false,
        dataType,
      });
      setDrawerOpen(true);
    } catch (e) {
      const detail = (e as { message?: string })?.message;
      toast.error(detail || "Recherche fehlgeschlagen");
    } finally {
      setResearching(null);
    }
  };

  const getVal = (companyId: string, key: string): CompanyValue | undefined =>
    (valuesMap.get(companyId) ?? []).find((v) => v.value_key === key);

  const convertCurrency = (val: number | string | null, from: string | null): number | null => {
    if (val == null) return null;
    const num = typeof val === "string" ? parseFloat(val) : val;
    if (isNaN(num)) return null;
    if (!from) return num;
    const f = fxRates[from];
    const t = fxRates[displayCurrency];
    if (f === undefined || t === undefined) return null;
    return f === t ? num : (num / f) * t;
  };

  const isCumMode = period.value === "CUM";
  const showPrevYear = period.value === "FY" && period.year !== undefined;
  const prevYear = showPrevYear ? (period.year as number) - 1 : null;

  const HIDDEN_IN_CUM_SECTIONS = new Set(["STAMMDATEN"]);
  const grouped = CATEGORY_ORDER.filter((cat) => !(isCumMode && HIDDEN_IN_CUM_SECTIONS.has(cat))).map((cat) => {
    const allDefs = definitions.filter((d) => d.category === cat).sort((a, b) => a.sort_order - b.sort_order);
    const factorDefs = allDefs.filter((d) => FACTOR_KEYS.has(d.key));
    const inputDefs = allDefs.filter((d) => !FACTOR_KEYS.has(d.key));
    const isExpanded = !isCumMode && expandedSections.has(cat);
    // actual_return ist FY-spezifisch (Yahoo MCap-Anker), CUM rechnet ueber mehrere FYs.
    const cumFactorDefs = factorDefs.filter((d) => d.key !== "market_cap" && d.key !== "actual_return");
    const baseVisibleDefs = isCumMode ? cumFactorDefs : (isExpanded ? allDefs : factorDefs);
    const visibleSectionDefs: (ValueDefinition & { isPrevYear?: boolean; basedOnKey?: string })[] = [];
    for (const d of baseVisibleDefs) {
      visibleSectionDefs.push(d);
      if (showPrevYear && isExpanded && PREV_YEAR_DISPLAY_KEYS.has(d.key)) {
        visibleSectionDefs.push({
          ...d,
          key: `${d.key}__prev`,
          label_en: `${d.label_en} (FY${prevYear})`,
          label_de: `${d.label_de} (FY${prevYear})`,
          isPrevYear: true,
          basedOnKey: d.key,
        });
      }
    }
    return {
      category: cat,
      label: CATEGORY_LABELS[cat],
      defs: visibleSectionDefs,
      hiddenInputCount: isCumMode ? 0 : inputDefs.length,
      isExpanded,
      isEmptyInCompact: factorDefs.length === 0,
    };
  }).filter((g) => g.defs.length > 0 || g.hiddenInputCount > 0);

  const visibleDefs = grouped.flatMap((g) => g.defs);

  if (!user) return null;

  return (
    <div className="min-h-screen bg-background">
      <AppHeader email={user.email} onLogout={logout} />
      <main className="p-6">
        <div className="mb-4">
          <Link to="/" className="inline-flex items-center gap-1 text-sm text-muted-foreground transition-colors hover:text-foreground">
            <ChevronLeft className="h-3.5 w-3.5" />
            {t.portfolios}
          </Link>
        </div>

        <div className="mb-4 flex flex-wrap items-end justify-between gap-4">
          <div className="flex items-center gap-3">
            <h2 className="text-2xl font-semibold tracking-tight text-foreground">{t.dashboard}</h2>
          </div>
          <div className="flex items-center gap-3">
            <div className="flex flex-wrap items-center gap-3">
              <div className="flex items-center gap-1">
                <span className="mr-1 text-[10px] font-semibold uppercase tracking-wide text-muted-foreground">Estimate</span>
                {FY_OPTIONS.filter((p) => p.year >= new Date().getFullYear()).map((p) => {
                  const i = FY_OPTIONS.indexOf(p);
                  const isActive = periodMode === "FY" && fyIdx === i;
                  return (
                    <button key={`est-${i}`}
                      onClick={() => { setPeriodMode("FY"); setFyIdx(i); }}
                      title="Laufendes FY — Werte basieren auf Q-Faktor-Schätzungen aus Quartalsberichten"
                      className={`rounded-md px-2 py-1 text-[11px] font-medium transition-colors ${isActive ? "bg-violet-600 text-white" : "border border-violet-300 bg-violet-50 text-violet-800 hover:bg-violet-100"}`}
                    >{p.label.replace("FY ", "")}</button>
                  );
                })}
              </div>
              <div className="h-6 w-px bg-border" />
              <div className="flex items-center gap-1">
                <span className="mr-1 text-[10px] font-semibold uppercase tracking-wide text-muted-foreground">Abgeschlossen</span>
                {FY_OPTIONS.filter((p) => p.year < new Date().getFullYear()).map((p) => {
                  const i = FY_OPTIONS.indexOf(p);
                  const isActive = periodMode === "FY" && fyIdx === i;
                  return (
                    <button key={`hist-${i}`}
                      onClick={() => { setPeriodMode("FY"); setFyIdx(i); }}
                      className={`rounded-md px-2 py-1 text-[11px] font-medium transition-colors ${isActive ? "bg-primary text-primary-foreground" : "border border-border bg-background text-foreground hover:bg-muted"}`}
                    >{p.label.replace("FY ", "")}</button>
                  );
                })}
              </div>
              <div className="h-6 w-px bg-border" />
              <div className="flex items-center gap-1">
                <span className="mr-1 text-[10px] font-semibold uppercase tracking-wide text-muted-foreground">Kumuliert</span>
                {CUM_OPTIONS.map((p, i) => {
                  const isActive = periodMode === "CUM" && cumIdx === i;
                  let missingCount = 0;
                  for (const c of companies) {
                    const av = availabilityMap.get(c.id);
                    if (!av?.has_snapshot_market_cap) { missingCount++; continue; }
                    for (let y = p.from_year - 1; y <= p.to_year; y++) {
                      const isPre = y === p.from_year - 1;
                      const requiredKeys = isPre ? ["net_income"] : CUM_INPUT_FY_KEYS;
                      const have = new Set(av.keys_per_year?.[String(y)] ?? []);
                      if (requiredKeys.some((k) => !have.has(k))) { missingCount++; break; }
                    }
                  }
                  const isUnavailable = companies.length > 0 && missingCount > 0;
                  return (
                    <button key={i}
                      onClick={() => { setPeriodMode("CUM"); setCumIdx(i); }}
                      title={isUnavailable ? `${missingCount}/${companies.length} Firmen brauchen Refresh` : `Kumuliert FY${p.from_year} bis FY${p.to_year}`}
                      className={`rounded-md px-2 py-1 text-[11px] font-medium transition-colors ${isActive ? "bg-primary text-primary-foreground" : "border border-border bg-background text-foreground hover:bg-muted"} ${isUnavailable && !isActive ? "opacity-50" : ""}`}
                    >
                      {p.label}
                      {isUnavailable && <span className="ml-1 text-amber-600">⚠</span>}
                    </button>
                  );
                })}
              </div>
            </div>
            <div className="flex items-center gap-1.5">
              <select value={displayCurrency} onChange={(e) => setDisplayCurrency(e.target.value)}
                className="rounded-md border border-input bg-background px-3 py-1.5 text-sm text-foreground">
                {CURRENCIES.map((c) => <option key={c} value={c}>{c}</option>)}
              </select>
            </div>
          </div>
        </div>

        <div className="mb-4 flex items-center gap-3 rounded-lg border border-border/60 bg-muted/30 px-4 py-2.5">
          <div className="flex items-center gap-2">
            <div className="h-2.5 w-2.5 rounded-full bg-primary" />
            <span className="text-sm font-medium text-foreground">{period.label}</span>
          </div>
          <span className="text-xs text-muted-foreground">|</span>
          <span className="text-xs text-muted-foreground">
            {period.value === "CUM"
              ? `Kumuliert über ${period.to_year - period.from_year + 1} FYs (${period.from_year}-${period.to_year}) · MCap-Anker: Anfang FY${period.from_year} (= Ende FY${period.from_year - 1}) · Cell zeigt Σ über die Periode + p.a.-Durchschnitt`
              : `Finanzdaten: Geschäftsjahr ${period.year} · MCap-Anker: Anfang FY (= Ende FY${(period.year ?? 0) - 1})`}
          </span>
          {isLoadingPeriod && (
            <span className="ml-auto flex items-center gap-1.5 text-xs text-primary">
              <Loader2 className="h-3.5 w-3.5 animate-spin" />
              Lade Daten…
            </span>
          )}
        </div>

        {period.value === "FY" && period.year !== undefined && period.year < new Date().getFullYear() && (() => {
          const locked = companies.filter((c) => isHohnLocked(availabilityMap.get(c.id), period.year));
          if (locked.length === 0) return null;
          return (
            <div className="mb-4 rounded-lg border border-amber-300 bg-amber-50 px-4 py-3 text-xs text-amber-900">
              <div className="flex items-center gap-2 font-semibold">
                <Lock className="h-4 w-4 text-amber-600" />
                Hohn-Rendite gesperrt für {locked.length} {locked.length === 1 ? "Firma" : "Firmen"} (FY{period.year})
              </div>
              <p className="mt-1 text-[11px] text-amber-800/90">
                Bei Non-US-Unternehmen ist die Hohn-Rendite für abgeschlossene FYs nur berechenbar, wenn ein
                fertig extrahierter Annual Report vorliegt. Lade den Geschäftsbericht in der Firmen-Verwaltung hoch.
              </p>
              <div className="mt-2 flex flex-wrap gap-1.5">
                {locked.map((c) => (
                  <Link key={c.id} to={`/portfolios/${pid}/manage`}
                    className="rounded border border-amber-200 bg-white/70 px-2 py-0.5 font-mono text-[11px] font-medium text-amber-900 hover:bg-white"
                    title="Annual Report hochladen"
                  >
                    {c.ticker}
                  </Link>
                ))}
              </div>
            </div>
          );
        })()}

        {period.value === "CUM" && (() => {
          type Gap = { company: Company; mcapMissing: boolean; missingPerYear: { year: number; isPre: boolean; missingKeys: string[] }[] };
          const gaps: Gap[] = [];
          for (const c of companies) {
            const av = availabilityMap.get(c.id);
            const mcapMissing = !av?.has_snapshot_market_cap;
            const missingPerYear: Gap["missingPerYear"] = [];
            for (let y = period.from_year - 1; y <= period.to_year; y++) {
              const isPre = y === period.from_year - 1;
              const required = isPre ? ["net_income"] : CUM_INPUT_FY_KEYS;
              const have = new Set(av?.keys_per_year?.[String(y)] ?? []);
              const miss = required.filter((k) => !have.has(k));
              if (miss.length > 0) missingPerYear.push({ year: y, isPre, missingKeys: miss });
            }
            if (mcapMissing || missingPerYear.length > 0) {
              gaps.push({ company: c, mcapMissing, missingPerYear });
            }
          }
          if (gaps.length === 0) return null;
          return (
            <div className="mb-4 rounded-lg border border-amber-300 bg-amber-50 px-4 py-3 text-xs text-amber-900">
              <div className="flex items-center gap-2 font-semibold">
                <AlertTriangle className="h-4 w-4 text-amber-600" />
                Daten unvollständig — {gaps.length} von {companies.length} Firmen brauchen Refresh
              </div>
              <p className="mt-1 text-[11px] text-amber-800/90">
                Pre-Period FY{period.from_year - 1} braucht nur <code className="rounded bg-amber-100 px-1">net_income</code>. Periode FY{period.from_year}-{period.to_year} braucht alle 10 Cum-Inputs (FCF, Net Income, SBC, Buybacks, Dividends, Cash/Marketable, Leases, LT-Debt). MCap kommt aus SNAPSHOT.
              </p>
              <div className="mt-2 space-y-2">
                {gaps.map(({ company, mcapMissing, missingPerYear }) => (
                  <div key={company.id} className="rounded border border-amber-200 bg-white/60 p-2">
                    <div className="flex items-start justify-between gap-2">
                      <div className="flex-1 min-w-0">
                        <div className="text-xs font-semibold text-amber-900">
                          {company.name} <span className="text-amber-700">({company.ticker})</span>
                        </div>
                        {mcapMissing && (
                          <div className="mt-1 text-[11px] text-amber-800">
                            SNAPSHOT: <span className="font-mono">market_cap</span> fehlt
                          </div>
                        )}
                        {missingPerYear.map(({ year, isPre, missingKeys }) => (
                          <div key={year} className="mt-1 text-[11px] text-amber-800">
                            <span className="font-mono font-semibold">FY{year}</span>
                            {isPre && <span className="text-amber-700"> (pre-period)</span>}:{" "}
                            <span className="font-mono">{missingKeys.join(", ")}</span>
                          </div>
                        ))}
                      </div>
                      <button
                        onClick={async () => {
                          if (mcapMissing) {
                            try { await refreshValues(company.id, ["market_cap"], "SNAPSHOT"); } catch {}
                          }
                          for (const { year, missingKeys } of missingPerYear) {
                            try {
                              await refreshValues(company.id, missingKeys, "FY", year);
                            } catch {}
                          }
                          await loadAllValues();
                        }}
                        className="shrink-0 rounded bg-amber-600 px-2.5 py-1 text-[11px] font-medium text-white transition-colors hover:bg-amber-700"
                        title="Triggert gezielten Refresh nur für die fehlenden Werte"
                      >
                        Fehlende holen
                      </button>
                    </div>
                  </div>
                ))}
              </div>
            </div>
          );
        })()}

        {companies.some((c) => refreshStatuses.get(c.id)?.status === "running") && (
          <div className="mb-4 space-y-2">
            {companies
              .filter((c) => refreshStatuses.get(c.id)?.status === "running")
              .map((c) => (
                <RefreshProgressBar
                  key={c.id}
                  companyName={c.name}
                  status={refreshStatuses.get(c.id)!}
                />
              ))}
          </div>
        )}

        <div className="relative overflow-x-auto rounded-xl border border-border/60 bg-card">
          {isLoadingPeriod && valuesMap.size > 0 && (
            <div className="pointer-events-none absolute inset-0 z-30 flex items-start justify-center bg-white/40 pt-16">
              <span className="flex items-center gap-2 rounded-full border border-border bg-white/95 px-3 py-1.5 text-xs font-medium text-primary shadow-sm">
                <Loader2 className="h-3.5 w-3.5 animate-spin" />
                Lade {period.label}…
              </span>
            </div>
          )}
          {isLoadingPeriod && valuesMap.size === 0 && (
            <div className="flex items-center justify-center px-6 py-16">
              <div className="flex flex-col items-center gap-2 text-muted-foreground">
                <Loader2 className="h-6 w-6 animate-spin text-primary" />
                <span className="text-sm">Lade Werte für {period.label}…</span>
              </div>
            </div>
          )}
          <table className={`w-full text-sm ${isLoadingPeriod && valuesMap.size === 0 ? "hidden" : ""}`}>
            <thead>
              {/* Category header row */}
              <tr>
                <th className="sticky left-0 z-20 border-b border-r bg-card px-3 py-2 text-left text-xs font-semibold text-foreground" rowSpan={2}>
                  Firma
                </th>
                {grouped.map((g) => {
                  const colSpan = g.defs.length === 0 ? 1 : g.defs.length;
                  const canExpand = g.hiddenInputCount > 0;
                  return (
                    <th
                      key={g.category}
                      colSpan={colSpan}
                      className={`select-none border-b border-r px-3 py-2 text-center text-xs font-semibold uppercase tracking-wider transition-colors ${canExpand ? "cursor-pointer hover:opacity-80" : ""} ${CATEGORY_COLORS[g.category]}`}
                      onClick={canExpand ? () => toggleCategory(g.category) : undefined}
                      title={canExpand ? (g.isExpanded ? "Hilfswerte ausblenden" : `${g.hiddenInputCount} Hilfswerte einblenden`) : undefined}
                    >
                      <div className="flex items-center justify-center gap-1.5">
                        {canExpand && (g.isExpanded ? <ChevronDown className="h-3.5 w-3.5" /> : <ChevronRight className="h-3.5 w-3.5" />)}
                        <span>{g.label}</span>
                        {canExpand && !g.isExpanded && (
                          <span className="rounded-full bg-foreground/10 px-1.5 py-0.5 text-[9px] font-semibold leading-none">+{g.hiddenInputCount}</span>
                        )}
                      </div>
                    </th>
                  );
                })}
              </tr>
              {/* Value column headers (or empty placeholder for sections without factors in compact mode) */}
              <tr>
                {grouped.flatMap((g) => {
                  if (g.defs.length === 0) {
                    return [
                      <th key={`${g.category}-empty`}
                        className="border-b border-r border-border/40 px-2 py-1.5 text-center text-[10px] italic text-muted-foreground">
                        nur Hilfswerte
                      </th>
                    ];
                  }
                  return g.defs.map((d) => (
                    <th key={d.key}
                      className={`whitespace-nowrap border-b border-r border-border/40 px-3 py-2 text-left text-xs font-medium text-muted-foreground ${d.isPrevYear ? "bg-muted/20 italic" : ""}`}>
                      <span className="truncate" title={d.label_de}>{d.label_en}</span>
                    </th>
                  ));
                })}
              </tr>
            </thead>
            <tbody>
              {companies.map((company) => (
                <tr key={company.id} className="border-b border-border/30 last:border-b-0 hover:bg-muted/20">
                  <td className="sticky left-0 z-10 whitespace-nowrap border-r bg-card px-3 py-2 font-medium text-foreground">
                    {(() => {
                      const av = availabilityMap.get(company.id);
                      const isRunning = refreshStatuses.get(company.id)?.status === "running";
                      const hasAnyAR = (av?.annual_report_years.length ?? 0) > 0;
                      const isUS = av?.is_us ?? false;
                      const canCompute = isUS || hasAnyAR;
                      const disabledReason = canCompute
                        ? null
                        : "Lade zuerst mindestens einen Annual Report hoch (Non-US-Firma).";
                      return (
                        <div className="flex items-center gap-2">
                          <span>{company.name}</span>
                          <span className="rounded bg-primary/10 px-1.5 py-0.5 font-mono text-[10px] font-medium text-primary">
                            {company.ticker}
                          </span>
                          <button
                            onClick={() => handleRefreshCompany(company)}
                            disabled={isRunning || !canCompute}
                            title={disabledReason ?? "Werte für diese Firma neu berechnen"}
                            className="ml-1 inline-flex shrink-0 items-center gap-1 rounded-md border border-primary/40 bg-primary/5 px-2 py-1 text-[11px] font-medium text-primary transition-colors hover:bg-primary/10 disabled:cursor-not-allowed disabled:opacity-40 disabled:hover:bg-primary/5"
                          >
                            <RefreshCw className={`h-3 w-3 ${isRunning ? "animate-spin" : ""}`} />
                            {isRunning ? "Berechnet…" : "Werte berechnen"}
                          </button>
                        </div>
                      );
                    })()}
                  </td>
                  {grouped.flatMap((g) => {
                    if (g.defs.length === 0) {
                      return [
                        <td key={`${company.id}-${g.category}-empty`}
                          className="border-r border-border/40 px-2 py-2 text-center text-muted-foreground/30">
                          —
                        </td>
                      ];
                    }
                    return g.defs.map((d) => {
                      if (d.isPrevYear) {
                        const baseKey = d.basedOnKey as string;
                        const prevRows = prevYearValuesMap.get(company.id) ?? [];
                        const prevCv = prevRows.find((v) => v.value_key === baseKey);
                        const prevRawStr = prevCv?.numeric_value ?? null;
                        const prevRaw = prevRawStr == null ? null : (typeof prevRawStr === "string" ? parseFloat(prevRawStr) : prevRawStr);
                        const prevValid = prevRaw != null && !isNaN(prevRaw) ? prevRaw : null;
                        const shouldConvertPrev = d.is_currency && d.data_type === "NUMERIC" && prevCv?.currency;
                        const convertedPrev = shouldConvertPrev ? convertCurrency(prevValid, prevCv?.currency ?? null) : prevValid;
                        const fxUnknownPrev = shouldConvertPrev && prevValid !== null && convertedPrev === null;
                        const displayPrev = fxUnknownPrev ? prevValid : convertedPrev;
                        return (
                          <td key={`${company.id}-${d.key}`}
                            className="whitespace-nowrap border-r border-border/40 bg-muted/20 px-3 py-2 tabular text-muted-foreground"
                            title={`Vorjahres-Wert ${d.label_de}`}
                          >
                            <span className="font-mono text-sm italic">
                              {prevValid == null ? "—" : formatValue(displayPrev, d.unit, displayCurrency)}
                            </span>
                          </td>
                        );
                      }
                      if (isCumMode) {
                        const cumResp = cumulativeMap.get(company.id);
                        const cell = cumResp?.values?.[d.key];
                        const cumVal = cell?.cum != null ? parseFloat(cell.cum) : null;
                        const avgVal = cell?.pa_avg != null ? parseFloat(cell.pa_avg) : null;
                        const isPartial = (cell?.missing?.length ?? 0) > 0;
                        const tier = colorTier(d.key, avgVal);
                        const tierBg = isPartial ? "bg-amber-50/40" : (tier ? TIER_BG[tier] : "");
                        const fmt = (n: number | null) => n == null || isNaN(n) ? "—" : `${n.toFixed(2)} %`;
                        const fmtPa = (n: number | null) => n == null || isNaN(n) ? "—" : `${n.toFixed(2)} % p.a.`;
                        return (
                          <td key={`${company.id}-${d.key}`}
                            className={`whitespace-nowrap border-r border-border/40 px-3 py-2 tabular cursor-pointer hover:bg-muted/30 ${tierBg}`}
                            title={isPartial ? `Partial — fehlt: ${cell?.missing.join("; ")}` : `${d.label_de} (Kumuliert ${period.from_year}-${period.to_year})`}
                            onClick={() => {
                              if (!cumResp) return;
                              setCumDrawer({
                                companyId: company.id,
                                companyName: company.name,
                                valueKey: d.key,
                                valueLabel: `${d.label_en} (Kumuliert ${period.from_year}-${period.to_year})`,
                              });
                              setCumDrawerOpen(true);
                            }}
                          >
                            <div className="flex flex-col gap-0.5">
                              <div className="flex items-center gap-1">
                                <span className="font-mono text-sm font-semibold text-foreground">{fmt(cumVal)}</span>
                                {isPartial && <AlertTriangle className="h-3 w-3 text-amber-600" />}
                              </div>
                              <span className="text-[10px] text-muted-foreground">{fmtPa(avgVal)}</span>
                            </div>
                          </td>
                        );
                      }
                      const av = availabilityMap.get(company.id);
                      const hohnLockedHere = HOHN_LOCKED_KEYS.has(d.key)
                        && period.value === "FY"
                        && isHohnLocked(av, period.year);
                      if (hohnLockedHere) {
                        return (
                          <td key={`${company.id}-${d.key}`}
                            className="whitespace-nowrap border-r border-border/40 bg-amber-50/70 px-3 py-2 cursor-help"
                            title={`Hohn-Rendite gesperrt — kein Annual Report für FY${period.year} hochgeladen. Lade den Geschäftsbericht hoch, dann lassen sich Werte verifizieren und Hohn-Rendite berechnen.`}
                          >
                            <div className="flex items-center gap-1.5 text-amber-800">
                              <Lock className="h-3.5 w-3.5 shrink-0" />
                              <span className="text-xs font-medium">Annual Report fehlt</span>
                            </div>
                          </td>
                        );
                      }

                      const cv = getVal(company.id, d.key);
                      const rawStr = cv?.numeric_value ?? null;
                      const raw: number | null = rawStr == null ? null : (typeof rawStr === "string" ? parseFloat(rawStr) : rawStr);
                      const rawValid = raw != null && !isNaN(raw) ? raw : null;
                      const shouldConvert = d.is_currency && d.data_type === "NUMERIC" && cv?.currency;
                      const convertedVal = shouldConvert ? convertCurrency(rawValid, cv?.currency ?? null) : rawValid;
                      const fxUnknown = shouldConvert && rawValid !== null && convertedVal === null;
                      const displayVal = fxUnknown ? rawValid : convertedVal;
                      const isQualitative = d.source_type === "QUALITATIVE";
                      const isCalculated = d.source_type === "CALCULATED";

                      const isHistoricalQual = isQualitative && period.value === "FY";

                      const isEditing = editCell?.companyId === company.id && editCell?.key === d.key;

                      const handleSaveEdit = async () => {
                        if (!editCell || saving) return;
                        const num = parseNumericInput(editCell.value);
                        if (isNaN(num)) { setEditCell(null); return; }
                        setSaving(true);
                        const defForKey = definitions.find((def) => def.key === d.key);
                        const effPeriodType = (isQualitative || defForKey?.always_current) ? "SNAPSHOT" : period.value;
                        const effPeriodYear = (isQualitative || defForKey?.always_current) ? undefined : period.year;
                        try {
                          await overrideValue(company.id, d.key, { numeric_value: num, source_name: "Manuell" }, effPeriodType, effPeriodYear);
                          await loadAllValues();
                        } finally {
                          setSaving(false);
                          setEditCell(null);
                        }
                      };

                      const isAlwaysCurrent = d.always_current === true;
                      const fyTier = colorTier(d.key, displayVal);
                      const fyTierBg = fyTier ? TIER_BG[fyTier] : "";
                      const isStammdatenKey = d.category === "STAMMDATEN";
                      const showFyAsOf = isStammdatenKey && cv?.period_type === "FY" && cv?.period_year != null;
                      // Try parsing as-of date from source_name (e.g. "Adj Close 30.09.2025") — most reliable
                      const sourceDateMatch = cv?.source_name?.match(/(\d{2}\.\d{2}\.\d{4})/);
                      const fyAsOfBadge = showFyAsOf
                        ? (sourceDateMatch?.[1]
                            ?? (company.fiscal_year_end_day && company.fiscal_year_end_month
                                ? `${String(company.fiscal_year_end_day).padStart(2, "0")}.${String(company.fiscal_year_end_month).padStart(2, "0")}.${cv?.period_year}`
                                : `FY${cv?.period_year}`))
                        : null;
                      // Partial-Hohn detect: if any required component for hohn_return_simple/detailed is missing
                      let fyPartialMissing: string[] = [];
                      if (period.value === "FY" && (d.key === "hohn_return_simple" || d.key === "hohn_return_detailed")) {
                        const requiredKeys = d.key === "hohn_return_simple"
                          ? ["fcf_yield", "ni_growth", "sbc_yield", "net_debt_change_pct"]
                          : ["dividend_yield", "ni_growth", "net_buyback_yield", "net_debt_change_pct"];
                        fyPartialMissing = requiredKeys.filter((k) => {
                          const c = getVal(company.id, k);
                          const v = c?.numeric_value;
                          return v == null || (typeof v === "string" && v === "");
                        });
                      }
                      const fyIsPartial = fyPartialMissing.length > 0 && cv?.numeric_value != null;

                      return (
                        <td key={`${company.id}-${d.key}`}
                          className={`whitespace-nowrap border-r border-border/40 px-3 py-2 tabular ${isCalculated ? "" : "cursor-pointer hover:bg-muted/30"} ${isHistoricalQual ? "bg-amber-50/50" : ""} ${isCalculated && !fyTier ? "bg-muted/10" : ""} ${fyTierBg} ${fyIsPartial ? "bg-amber-50/40" : ""}`}
                          title={fyIsPartial ? `Partial — fehlende Komponenten: ${fyPartialMissing.join(", ")}` : (isCalculated ? "Berechneter Wert (Formel) — Diskussion gehoert zu den Eingangswerten." : undefined)}
                          onClick={isCalculated ? undefined : () => {
                            setDrawer({
                              companyId: company.id,
                              valueKey: d.key,
                              companyName: company.name,
                              valueLabel: d.label_en,
                              currentScore: raw,
                              currentText: cv?.text_value ?? undefined,
                              isQualitative,
                              isAlwaysCurrent,
                              isCalculated,
                              dataType: d.data_type,
                            });
                            setDrawerOpen(true);
                          }}
                          onDoubleClick={isCalculated ? undefined : (e) => {
                            e.stopPropagation();
                            const currentVal = cv?.numeric_value != null ? String(cv.numeric_value) : "";
                            setEditCell({ companyId: company.id, key: d.key, value: currentVal });
                          }}
                        >
                          {isEditing ? (
                            <input
                              autoFocus
                              type="text"
                              className="w-20 rounded border border-primary bg-background px-1.5 py-0.5 font-mono text-sm text-foreground outline-none"
                              value={editCell.value}
                              onChange={(e) => setEditCell({ ...editCell, value: e.target.value })}
                              onKeyDown={(e) => {
                                if (e.key === "Enter") handleSaveEdit();
                                if (e.key === "Escape") setEditCell(null);
                              }}
                              onBlur={handleSaveEdit}
                            />
                          ) : (
                          <div className="flex items-center gap-1.5">
                            {isQualitative && (
                              <Sparkles className="h-3 w-3 shrink-0 text-primary/60" />
                            )}
                            {(() => {
                              const pdfNullSource = cv?.numeric_value == null && cv?.text_value == null
                                && typeof cv?.source_name === "string" && cv.source_name.includes("kein Wert");
                              const cvEmpty = cv && cv.numeric_value == null && cv.text_value == null && !pdfNullSource;
                              const noCv = !cv && !isQualitative;
                              const isMissing = noCv || pdfNullSource || cvEmpty || notFound.has(`${company.id}:${d.key}`);
                              if (!isMissing) return null;
                              const reasonMatch = pdfNullSource ? cv?.source_name?.match(/kein Wert:\s*(.+)$/) : null;
                              const pdfReason = reasonMatch?.[1]?.trim();
                              const isCalc = d.source_type === "CALCULATED";
                              const canResearch = !isCalc && !isQualitative;
                              const tooltipText = pdfNullSource
                                ? `Annual Report analysiert, kein Wert für diese Kennzahl gefunden${pdfReason ? `: ${pdfReason}` : ""}.`
                                : isCalc
                                ? `Berechnung nicht möglich - benötigte Eingabewerte fehlen${FORMULAS[d.key] ? ` (${FORMULAS[d.key]})` : ""}`
                                : `Wert fehlt - weder im PDF noch von Yahoo/EDGAR.`;
                              const labelText = pdfNullSource
                                ? "Im Bericht nicht gefunden"
                                : isCalc ? "Inputs fehlen" : "Wert nicht gefunden";
                              const researchKey = `${company.id}:${d.key}`;
                              const isResearching = researching === researchKey;
                              return (
                                <div className="group/nf flex items-center gap-1.5" title={tooltipText}>
                                  <AlertTriangle className="h-3.5 w-3.5 text-amber-600" />
                                  <span className="text-xs text-amber-700">{labelText}</span>
                                  {canResearch && (
                                    <button
                                      onClick={(e) => {
                                        e.stopPropagation();
                                        handleResearch(company.id, company.name, d.key, d.label_en, d.data_type);
                                      }}
                                      disabled={isResearching || researching !== null}
                                      className="ml-0.5 inline-flex items-center gap-0.5 rounded border border-primary/40 bg-primary/5 px-1.5 py-0.5 text-[10px] font-medium text-primary transition-colors hover:bg-primary/10 disabled:opacity-50 disabled:cursor-not-allowed"
                                      title="Mit Claude Web-Recherche den Wert finden"
                                    >
                                      {isResearching ? (
                                        <Loader2 className="h-3 w-3 animate-spin" />
                                      ) : (
                                        <Search className="h-3 w-3" />
                                      )}
                                      {isResearching ? "Recherche…" : "Recherchieren"}
                                    </button>
                                  )}
                                </div>
                              );
                            })() ?? (
                            <>
                              <span className="font-mono text-sm text-foreground">
                                {d.data_type === "TEXT"
                                  ? cv?.text_value ?? (cv?.numeric_value != null ? parseFloat(String(cv.numeric_value)).toFixed(2) : t.noValue)
                                  : d.data_type === "FACTOR"
                                  ? cv?.numeric_value != null ? parseFloat(String(cv.numeric_value)).toFixed(2) : (cv?.text_value ?? t.noValue)
                                  : formatValue(displayVal, d.unit, displayCurrency)}
                              </span>
                              {fxUnknown && (
                                <span
                                  title={`Wechselkurs ${cv?.currency} → ${displayCurrency} unbekannt, Wert bleibt in ${cv?.currency}`}
                                  className="shrink-0 rounded bg-amber-100 px-1 py-0.5 text-[10px] font-semibold text-amber-800"
                                >
                                  {cv?.currency}
                                </span>
                              )}
                              {fyIsPartial && (
                                <AlertTriangle className="h-3 w-3 shrink-0 text-amber-600"
                                  aria-label={`Partial: ${fyPartialMissing.join(", ")} fehlen`} />
                              )}
                              {fyAsOfBadge && (
                                <span
                                  title={`Stand FY${cv?.period_year} (${fyAsOfBadge})`}
                                  className="shrink-0 rounded bg-blue-50 px-1 py-0.5 text-[9px] font-medium text-blue-700"
                                >
                                  {fyAsOfBadge}
                                </span>
                              )}
                              {cv?.is_forecast && (
                                <span
                                  title={cv.source_name ? `Schätzung — ${cv.source_name}` : "Schätzung (kein Ist-Wert verfügbar)"}
                                  className="shrink-0 rounded bg-violet-100 px-1 py-0.5 text-[9px] font-bold text-violet-700"
                                >
                                  e
                                </span>
                              )}
                            </>
                            )}
                            {cv && !isQualitative && (
                              <button
                                onClick={(e) => {
                                  e.stopPropagation();
                                  const rect = (e.target as HTMLElement).getBoundingClientRect();
                                  const isOpen = tooltip?.key === d.key && tooltip?.companyId === company.id;
                                  setTooltip(isOpen ? null : { key: d.key, companyId: company.id, x: rect.left, y: rect.bottom + 6 });
                                }}
                                className="shrink-0 rounded p-0.5 text-muted-foreground/50 transition-colors hover:text-muted-foreground">
                                <Info className="h-3 w-3" />
                              </button>
                            )}
                          </div>
                          )}
                        </td>
                      );
                    });
                  })}
                </tr>
              ))}
              {companies.length === 0 && (
                <tr>
                  <td colSpan={visibleDefs.length + grouped.filter((g) => g.defs.length === 0).length + 1}
                    className="px-6 py-12 text-center text-sm text-muted-foreground">
                    Noch keine Firmen in diesem Portfolio.{" "}
                    <Link to={`/portfolios/${pid}/manage`} className="text-primary hover:underline">Firma hinzufügen</Link>
                  </td>
                </tr>
              )}
              {companies.length > 0 && (
                <tr className="border-t border-border/50 bg-muted/20 hover:bg-muted/40">
                  <td colSpan={visibleDefs.length + grouped.filter((g) => g.defs.length === 0).length + 1} className="px-3 py-2">
                    <Link to={`/portfolios/${pid}/manage`}
                      className="inline-flex items-center gap-1.5 text-xs font-medium text-muted-foreground transition-colors hover:text-foreground">
                      <Plus className="h-3.5 w-3.5" />
                      Firma hinzufügen / verwalten
                    </Link>
                  </td>
                </tr>
              )}
            </tbody>
          </table>
        </div>

        {drawer && (
          <AnalysisDrawer
            open={drawerOpen}
            onClose={() => setDrawerOpen(false)}
            companyId={drawer.companyId}
            companyName={drawer.companyName}
            valueKey={drawer.valueKey}
            valueLabel={drawer.valueLabel}
            currentScore={drawer.currentScore}
            currentText={drawer.currentText}
            dataType={drawer.dataType as "NUMERIC" | "TEXT" | "FACTOR"}
            isCalculated={drawer.isCalculated}
            periodType={period.value}
            periodYear={period.year}
            onAcceptScore={async (score, textValue) => {
              const effPeriodType = (drawer.isQualitative || drawer.isAlwaysCurrent) ? "SNAPSHOT" : period.value;
              const effPeriodYear = (drawer.isQualitative || drawer.isAlwaysCurrent) ? undefined : period.year;
              if (textValue !== undefined) {
                await overrideValue(drawer.companyId, drawer.valueKey, { text_value: textValue, source_name: "Manuell" }, effPeriodType, effPeriodYear);
              } else if (score != null) {
                await overrideValue(drawer.companyId, drawer.valueKey, { numeric_value: score, source_name: "Manuell" }, effPeriodType, effPeriodYear);
              }
              await loadAllValues();
              setDrawerOpen(false);
            }}
          />
        )}

        {cumDrawer && cumulativeMap.get(cumDrawer.companyId) && (
          <CumulativeBreakdownDrawer
            open={cumDrawerOpen}
            onClose={() => setCumDrawerOpen(false)}
            companyName={cumDrawer.companyName}
            valueKey={cumDrawer.valueKey}
            valueLabel={cumDrawer.valueLabel}
            cum={cumulativeMap.get(cumDrawer.companyId)!}
            onJumpToFy={(year) => {
              const targetIdx = FY_OPTIONS.findIndex((opt) => opt.year === year);
              if (targetIdx >= 0) {
                setPeriodMode("FY");
                setFyIdx(targetIdx);
              }
            }}
          />
        )}

        {tooltip && (() => {
          const cv = getVal(tooltip.companyId, tooltip.key);
          const def = definitions.find((d) => d.key === tooltip.key);
          const tooltipCompany = companies.find((c) => c.id === tooltip.companyId);
          if (!cv || !def) return null;

          const formatAsOf = (): string => {
            if (cv.period_type === "FY" && cv.period_year) {
              const m = tooltipCompany?.fiscal_year_end_month;
              const d = tooltipCompany?.fiscal_year_end_day;
              if (m && d) {
                return `${String(d).padStart(2, "0")}.${String(m).padStart(2, "0")}.${cv.period_year} (FY-Ende ${cv.period_year})`;
              }
              return `FY ${cv.period_year}`;
            }
            return "aktuell (Snapshot)";
          };

          const isClaudeResearch = cv.source_name?.includes("Claude-Recherche");
          const confidence = cv.manually_overridden
            ? { label: "Manuell überschrieben", color: "bg-amber-100 text-amber-800 border-amber-300", icon: Pencil }
            : isClaudeResearch
            ? { label: "KI-Recherche", color: "bg-orange-100 text-orange-800 border-orange-300", icon: Sparkles }
            : def.source_type === "API"
            ? { label: "Verifizierte Datenquelle", color: "bg-green-100 text-green-800 border-green-300", icon: ShieldCheck }
            : def.source_type === "CALCULATED"
            ? { label: "Berechnet", color: "bg-blue-100 text-blue-800 border-blue-300", icon: Calculator }
            : def.source_type === "QUALITATIVE"
            ? { label: "Qualitativ (Einschätzung)", color: "bg-amber-100 text-amber-800 border-amber-300", icon: MessageSquare }
            : { label: "Nutzereingabe", color: "bg-slate-100 text-slate-700 border-slate-300", icon: Pencil };

          const ConfIcon = confidence.icon;

          return createPortal(
            <>
              <div className="fixed inset-0 z-[99]" onClick={() => setTooltip(null)} />
              <div className="fixed z-[100] w-72 rounded-xl border border-border bg-card p-4 shadow-2xl shadow-black/10"
                style={{ left: Math.min(tooltip.x, window.innerWidth - 300), top: Math.min(tooltip.y, window.innerHeight - 250) }}>
                <div className="mb-3 flex items-center justify-between">
                  <span className="text-xs font-semibold text-foreground">{def.label_en}</span>
                  <button onClick={() => setTooltip(null)} className="rounded p-0.5 hover:bg-muted">
                    <X className="h-3.5 w-3.5 text-muted-foreground" />
                  </button>
                </div>
                <div className={`mb-3 flex items-center gap-2 rounded-lg border px-3 py-2 ${confidence.color}`}>
                  <ConfIcon className="h-4 w-4 shrink-0" />
                  <span className="text-xs font-medium">{confidence.label}</span>
                </div>
                {FORMULAS[tooltip.key] && (
                  <div className="mb-3 rounded-lg border border-blue-200 bg-blue-50 px-3 py-2">
                    <p className="text-[10px] font-medium text-blue-600 uppercase tracking-wide mb-0.5">Formel</p>
                    <p className="text-xs font-mono text-blue-800">{FORMULAS[tooltip.key]}</p>
                  </div>
                )}
                <dl className="space-y-2 text-xs">
                  <div className="flex justify-between">
                    <dt className="text-muted-foreground">{t.source}</dt>
                    <dd className="font-medium text-foreground text-right">{cv.source_name ?? "—"}</dd>
                  </div>
                  {cv.source_link && (
                    <div className="flex justify-between">
                      <dt className="text-muted-foreground">Link</dt>
                      <dd className="text-right">
                        <a href={cv.source_link} target="_blank" rel="noreferrer" className="text-primary hover:underline truncate max-w-[140px] inline-block">
                          {(() => { try { return new URL(cv.source_link).hostname; } catch { return cv.source_link; } })()}
                        </a>
                      </dd>
                    </div>
                  )}
                  <div className="flex justify-between">
                    <dt className="text-muted-foreground">Wert per</dt>
                    <dd className="font-medium text-foreground text-right">{formatAsOf()}</dd>
                  </div>
                  <div className="flex justify-between">
                    <dt className="text-muted-foreground">{t.fetchedAt}</dt>
                    <dd className="text-foreground">{cv.fetched_at ? new Date(cv.fetched_at).toLocaleString("de-DE") : "—"}</dd>
                  </div>
                  {cv.currency && (
                    <div className="flex justify-between">
                      <dt className="text-muted-foreground">Originalwährung</dt>
                      <dd className="font-mono text-foreground">{cv.currency}</dd>
                    </div>
                  )}
                </dl>
              </div>
            </>,
            document.body
          );
        })()}
      </main>
    </div>
  );
}
