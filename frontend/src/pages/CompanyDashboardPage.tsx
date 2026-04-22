import { useEffect, useState, useCallback } from "react";
import { Link, useParams } from "react-router-dom";
import { ChevronLeft, ChevronRight, ChevronDown, RefreshCw, Info, X, Plus, ShieldCheck, Calculator, MessageSquare, Pencil, Sparkles, AlertTriangle } from "lucide-react";
import { createPortal } from "react-dom";
import { AppHeader } from "@/components/AppHeader";
import { Button } from "@/components/ui/button";
import { useAuth } from "@/hooks/useAuth";
import { t } from "@/lib/i18n";
import { formatValue } from "@/lib/format";
import { listCompanies, type Company } from "@/api/companies";
import {
  getValueDefinitions,
  getCompanyValues,
  getRefreshStatus,
  refreshValues,
  overrideValue,
  calculateValues,
  type ValueDefinition,
  type CompanyValue,
  type RefreshStatus,
} from "@/api/values";
import { AnalysisDrawer } from "@/components/AnalysisDrawer";
import { RefreshProgressBar } from "@/components/RefreshProgressBar";
import { getFxRates } from "@/api/fx";
import { parseNumericInput } from "@/lib/parseNumeric";

const CATEGORY_ORDER = [
  "STAMMDATEN", "CASH_DEBT", "BUYBACKS_SBC", "FCF",
  "NI_GROWTH", "DELTA_ND", "DIVIDENDS", "HOHN_RETURN",
];

const CATEGORY_LABELS: Record<string, string> = {
  STAMMDATEN: "Stammdaten",
  CASH_DEBT: "Cash & Debt",
  BUYBACKS_SBC: "Buybacks & SBC",
  FCF: "FCF Yield",
  NI_GROWTH: "Net Income Growth",
  DELTA_ND: "ΔNet Debt",
  DIVIDENDS: "Dividends",
  HOHN_RETURN: "Hohn-Rendite",
};

const CATEGORY_COLORS: Record<string, string> = {
  STAMMDATEN: "bg-slate-100 text-slate-700 border-slate-200",
  CASH_DEBT: "bg-blue-50 text-blue-700 border-blue-200",
  BUYBACKS_SBC: "bg-amber-50 text-amber-700 border-amber-200",
  FCF: "bg-teal-50 text-teal-700 border-teal-200",
  NI_GROWTH: "bg-violet-50 text-violet-700 border-violet-200",
  DELTA_ND: "bg-rose-50 text-rose-700 border-rose-200",
  DIVIDENDS: "bg-emerald-50 text-emerald-700 border-emerald-200",
  HOHN_RETURN: "bg-sky-100 text-sky-800 border-sky-300",
};

const PERIOD_OPTIONS = [
  { label: "FY 2026e", value: "FY", year: 2026 },
  { label: "FY 2025", value: "FY", year: 2025 },
  { label: "FY 2024", value: "FY", year: 2024 },
  { label: "FY 2023", value: "FY", year: 2023 },
  { label: "FY 2022", value: "FY", year: 2022 },
  { label: "FY 2021", value: "FY", year: 2021 },
  { label: "FY 2020", value: "FY", year: 2020 },
];

const FALLBACK_FX_RATES: Record<string, number> = {
  USD: 1, EUR: 0.92, GBP: 0.79, CHF: 0.88, JPY: 155, KRW: 1390,
  HKD: 7.8, CNY: 7.2, CAD: 1.35, AUD: 1.52, SEK: 10.5, NOK: 10.5,
  DKK: 6.9, SGD: 1.34, INR: 83, BRL: 5.0, MXN: 17, ZAR: 18.5,
};
const CURRENCIES = ["USD", "EUR", "GBP", "CHF", "JPY", "KRW", "CNY", "HKD"];

const FORMULAS: Record<string, string> = {
  market_cap_calc: "Stock Price × Shares Outstanding",
  cash_sum: "Cash & Equivalents + Mkt Sec ST + LT",
  debt_sum: "Lease Liabilities + Long-term Debt",
  net_debt: "Debt Sum − Cash Sum",
  ev: "Market Cap + Net Debt",
  net_buyback: "Buyback-Volumen − SBC",
  sbc_yield: "SBC / Market Cap × 100",
  net_buyback_yield: "Net Buyback / Market Cap × 100",
  fcf_yield: "FCF / Market Cap × 100",
  ni_growth: "(NI[Y] / NI[Y−1] − 1) × 100",
  net_debt_change: "Net Debt[Y−1] − Net Debt[Y]",
  net_debt_change_pct: "ΔNet Debt / Market Cap × 100",
  dividend_yield: "Dividends / Market Cap × 100",
  hohn_return_simple: "FCF Yield + NI Growth − SBC/MCap + ΔND/MCap",
  hohn_return_detailed: "Dividend Yield + NI Growth + Net Buyback/MCap + ΔND/MCap",
};

type TooltipState = { key: string; companyId: string; x: number; y: number } | null;

export function CompanyDashboardPage() {
  const { pid } = useParams<{ pid: string }>();
  const { user, logout } = useAuth();

  const [definitions, setDefinitions] = useState<ValueDefinition[]>([]);
  const [companies, setCompanies] = useState<Company[]>([]);
  const [valuesMap, setValuesMap] = useState<Map<string, CompanyValue[]>>(new Map());
  const [periodIdx, setPeriodIdx] = useState(0);
  const [displayCurrency, setDisplayCurrency] = useState("USD");
  const [loadingKeys, setLoadingKeys] = useState<Set<string>>(new Set());
  const [collapsed, setCollapsed] = useState<Set<string>>(new Set());
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
    dataType: string;
  } | null>(null);
  const [drawerOpen, setDrawerOpen] = useState(false);
  const [refreshStatuses, setRefreshStatuses] = useState<Map<string, RefreshStatus>>(new Map());
  const [fxRates, setFxRates] = useState<Record<string, number>>(FALLBACK_FX_RATES);

  const period = PERIOD_OPTIONS[periodIdx];

  const loadAllValues = useCallback(async () => {
    if (!pid || companies.length === 0) return;
    const snapshotOverrideKeys = new Set(
      definitions
        .filter((d) => d.source_type === "QUALITATIVE" || d.always_current)
        .map((d) => d.key)
    );
    const map = new Map<string, CompanyValue[]>();
    await Promise.all(
      companies.map(async (c) => {
        const periodVals = await getCompanyValues(c.id, period.value, period.year);
        if (period.value !== "SNAPSHOT") {
          const snapshotVals = await getCompanyValues(c.id, "SNAPSHOT");
          const periodKeyMap = new Map(periodVals.map((v) => [v.value_key, v]));
          const allKeys = new Set([...periodVals.map((v) => v.value_key), ...snapshotVals.map((v) => v.value_key)]);
          const merged = [...allKeys].map((key) => {
            if (snapshotOverrideKeys.has(key)) {
              return snapshotVals.find((v) => v.value_key === key) ?? periodKeyMap.get(key);
            }
            return periodKeyMap.get(key) ?? snapshotVals.find((v) => v.value_key === key);
          }).filter(Boolean) as CompanyValue[];
          map.set(c.id, merged);
        } else {
          map.set(c.id, periodVals);
        }
      })
    );
    setValuesMap(map);
  }, [pid, companies, period.value, period.year, definitions]);

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

  useEffect(() => {
    setValuesMap(new Map());
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
    setCollapsed((prev) => {
      const next = new Set(prev);
      next.has(cat) ? next.delete(cat) : next.add(cat);
      return next;
    });
  };

  const handleRefreshColumn = async (key: string) => {
    const loadKey = key;
    setLoadingKeys((prev) => new Set([...prev, loadKey]));
    try {
      await Promise.all(
        companies.map(async (c) => {
          const updated = await refreshValues(c.id, [key], period.value, period.year);
          setValuesMap((prev) => {
            const next = new Map(prev);
            const existing = next.get(c.id) ?? [];
            const merged = new Map(existing.map((v) => [`${v.value_key}:${v.period_type}:${v.period_year}`, v]));
            for (const u of updated) merged.set(`${u.value_key}:${u.period_type}:${u.period_year}`, u);
            next.set(c.id, Array.from(merged.values()));
            return next;
          });
          const hasValue = updated.some((u) => u.value_key === key);
          const nfKey = `${c.id}:${key}`;
          if (!hasValue) {
            setNotFound((prev) => new Set([...prev, nfKey]));
          } else {
            setNotFound((prev) => { const n = new Set(prev); n.delete(nfKey); return n; });
          }
        })
      );
    } finally {
      setLoadingKeys((prev) => { const n = new Set(prev); n.delete(loadKey); return n; });
    }
  };

  const handleRefreshCompany = async (c: Company) => {
    const apiKeys = definitions.filter((d) => d.source_type === "API").map((d) => d.key);
    setLoadingKeys((prev) => new Set([...prev, ...apiKeys]));
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
      setLoadingKeys(new Set());
      await pollStatuses(companies);
      await loadAllValues();
    }
  };

  const handleRefreshAll = async () => {
    const apiKeys = definitions.filter((d) => d.source_type === "API").map((d) => d.key);
    setLoadingKeys(new Set(apiKeys));
    setRefreshStatuses(new Map(companies.map((c) => [c.id, { company_id: c.id, total: apiKeys.length, completed: 0, current_key: null, status: "running" as const }])));
    try {
      for (const c of companies) {
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
        }
        await pollStatuses(companies);
      }
      await loadAllValues();
      const checkableKeys = definitions
        .filter((d) => d.source_type === "API" || d.source_type === "CALCULATED")
        .map((d) => d.key);
      setNotFound((prev) => {
        const next = new Set(prev);
        for (const c of companies) {
          const vals = valuesMap.get(c.id) ?? [];
          const hasKeys = new Set(vals.map((v) => v.value_key));
          for (const k of checkableKeys) {
            const nfKey = `${c.id}:${k}`;
            hasKeys.has(k) ? next.delete(nfKey) : next.add(nfKey);
          }
        }
        return next;
      });
    } finally {
      setLoadingKeys(new Set());
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

  const grouped = CATEGORY_ORDER.map((cat) => ({
    category: cat,
    label: CATEGORY_LABELS[cat],
    defs: definitions.filter((d) => d.category === cat).sort((a, b) => a.sort_order - b.sort_order),
  })).filter((g) => g.defs.length > 0);

  const visibleDefs = grouped.flatMap((g) => collapsed.has(g.category) ? [] : g.defs);

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
          <h2 className="text-2xl font-semibold tracking-tight text-foreground">{t.dashboard}</h2>
          <div className="flex items-center gap-3">
            <Button variant="outline" size="sm" onClick={handleRefreshAll}>
              <RefreshCw className="mr-1.5 h-3.5 w-3.5" />
              {t.allValues}
            </Button>
            <Link to={`/portfolios/${pid}/manage`}>
              <Button variant="outline" size="sm">
                <Plus className="mr-1.5 h-3.5 w-3.5" />
                {t.companies}
              </Button>
            </Link>
            <select value={periodIdx} onChange={(e) => setPeriodIdx(Number(e.target.value))}
              className="rounded-md border border-input bg-background px-3 py-1.5 text-sm text-foreground">
              {PERIOD_OPTIONS.map((p, i) => <option key={i} value={i}>{p.label}</option>)}
            </select>
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
            <span className="text-sm font-medium text-foreground">
              {period.value === "SNAPSHOT" ? "Aktuelle Werte" : period.label}
            </span>
          </div>
          <span className="text-xs text-muted-foreground">|</span>
          <span className="text-xs text-muted-foreground">
            Finanzdaten: {period.value === "SNAPSHOT" ? "Live / letzte verfügbare" : period.value === "LTM" || period.value === "TTM" ? "Letzte 12 Monate" : `Geschäftsjahr ${period.year}`}
          </span>
          {period.value !== "SNAPSHOT" && (
            <>
              <span className="text-xs text-muted-foreground">|</span>
              <span className="text-xs italic text-amber-600">
                Qualitative Bewertungen aus heutiger Sicht · Leere Zellen = keine Daten für diesen Zeitraum
              </span>
            </>
          )}
        </div>

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

        <div className="overflow-x-auto rounded-xl border border-border/60 bg-card">
          <table className="w-full text-sm">
            <thead>
              {/* Category header row */}
              <tr>
                <th className="sticky left-0 z-20 border-b border-r bg-card px-3 py-2 text-left text-xs font-semibold text-foreground" rowSpan={2}>
                  Firma
                </th>
                {grouped.map((g) => (
                  <th
                    key={g.category}
                    colSpan={collapsed.has(g.category) ? 1 : g.defs.length}
                    className={`cursor-pointer select-none border-b border-r px-3 py-2 text-center text-xs font-semibold uppercase tracking-wider transition-colors hover:opacity-80 ${CATEGORY_COLORS[g.category]}`}
                    onClick={() => toggleCategory(g.category)}
                  >
                    <div className="flex items-center justify-center gap-1.5">
                      {collapsed.has(g.category)
                        ? <ChevronRight className="h-3.5 w-3.5" />
                        : <ChevronDown className="h-3.5 w-3.5" />
                      }
                      <span>{g.label}</span>
                    </div>
                  </th>
                ))}
              </tr>
              {/* Value column headers (only for expanded categories) */}
              <tr>
                {grouped.flatMap((g) => {
                  if (collapsed.has(g.category)) {
                    return [
                      <th key={`${g.category}-collapsed`}
                        className="border-b border-r border-border/40 px-2 py-1.5 text-center text-[10px] text-muted-foreground">
                        {g.defs.length} Werte
                      </th>
                    ];
                  }
                  return g.defs.map((d) => (
                    <th key={d.key}
                      className="whitespace-nowrap border-b border-r border-border/40 px-3 py-2 text-left text-xs font-medium text-muted-foreground">
                      <div className="flex items-center gap-1">
                        <span className="truncate" title={d.label_de}>{d.label_en}</span>
                        {d.source_type === "API" && (
                          <button onClick={() => handleRefreshColumn(d.key)}
                            disabled={loadingKeys.has(d.key)}
                            className="ml-auto shrink-0 rounded p-0.5 text-muted-foreground transition-colors hover:bg-muted hover:text-foreground disabled:opacity-40"
                            title={t.calculate}>
                            <RefreshCw className={`h-3 w-3 ${loadingKeys.has(d.key) ? "animate-spin" : ""}`} />
                          </button>
                        )}
                      </div>
                    </th>
                  ));
                })}
              </tr>
            </thead>
            <tbody>
              {companies.map((company) => (
                <tr key={company.id} className="border-b border-border/30 last:border-b-0 hover:bg-muted/20">
                  <td className="sticky left-0 z-10 whitespace-nowrap border-r bg-card px-3 py-2 font-medium text-foreground">
                    <div className="flex items-center gap-2">
                      <button
                        onClick={() => handleRefreshCompany(company)}
                        disabled={refreshStatuses.get(company.id)?.status === "running"}
                        title="Nur diese Firma berechnen"
                        className="shrink-0 rounded p-1 text-muted-foreground transition-colors hover:bg-muted hover:text-foreground disabled:opacity-40"
                      >
                        <RefreshCw className={`h-3.5 w-3.5 ${refreshStatuses.get(company.id)?.status === "running" ? "animate-spin" : ""}`} />
                      </button>
                      <span>{company.name}</span>
                      <span className="rounded bg-primary/10 px-1.5 py-0.5 font-mono text-[10px] font-medium text-primary">
                        {company.ticker}
                      </span>
                    </div>
                  </td>
                  {grouped.flatMap((g) => {
                    if (collapsed.has(g.category)) {
                      return [
                        <td key={`${company.id}-${g.category}-collapsed`}
                          className="border-r border-border/40 px-2 py-2 text-center text-muted-foreground/30">
                          ...
                        </td>
                      ];
                    }
                    return g.defs.map((d) => {
                      const cv = getVal(company.id, d.key);
                      const rawStr = cv?.numeric_value ?? null;
                      const raw: number | null = rawStr == null ? null : (typeof rawStr === "string" ? parseFloat(rawStr) : rawStr);
                      const rawValid = raw != null && !isNaN(raw) ? raw : null;
                      const shouldConvert = d.is_currency && d.data_type === "NUMERIC" && cv?.currency;
                      const convertedVal = shouldConvert ? convertCurrency(rawValid, cv?.currency ?? null) : rawValid;
                      const fxUnknown = shouldConvert && rawValid !== null && convertedVal === null;
                      const displayVal = fxUnknown ? rawValid : convertedVal;
                      const isQualitative = d.source_type === "QUALITATIVE";

                      const isHistoricalQual = isQualitative && period.value !== "SNAPSHOT";

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
                          await calculateValues(company.id, period.value, period.year);
                          await loadAllValues();
                        } finally {
                          setSaving(false);
                          setEditCell(null);
                        }
                      };

                      const isAlwaysCurrent = d.always_current === true;

                      return (
                        <td key={`${company.id}-${d.key}`}
                          className={`whitespace-nowrap border-r border-border/40 px-3 py-2 tabular cursor-pointer hover:bg-muted/30 ${isHistoricalQual ? "bg-amber-50/50" : ""}`}
                          onClick={() => {
                            setDrawer({
                              companyId: company.id,
                              valueKey: d.key,
                              companyName: company.name,
                              valueLabel: d.label_en,
                              currentScore: raw,
                              currentText: cv?.text_value ?? undefined,
                              isQualitative,
                              isAlwaysCurrent,
                              dataType: d.data_type,
                            });
                            setDrawerOpen(true);
                          }}
                          onDoubleClick={(e) => {
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
                            {!cv && notFound.has(`${company.id}:${d.key}`) ? (
                              <div className="group/nf flex items-center gap-1.5 cursor-pointer"
                                title={d.source_type === "CALCULATED"
                                  ? `Berechnung nicht möglich - benötigte Eingabewerte fehlen${FORMULAS[d.key] ? ` (${FORMULAS[d.key]})` : ""}`
                                  : "Nicht gefunden - Doppelklick zum manuellen Eintragen"}>
                                <AlertTriangle className="h-3.5 w-3.5 text-red-500" />
                                <span className="text-xs text-red-500">{d.source_type === "CALCULATED" ? "Inputs fehlen" : "Nicht gefunden"}</span>
                              </div>
                            ) : (
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
                  <td colSpan={visibleDefs.length + grouped.filter((g) => collapsed.has(g.category)).length + 1}
                    className="px-6 py-12 text-center text-sm text-muted-foreground">
                    Noch keine Firmen in diesem Portfolio.{" "}
                    <Link to={`/portfolios/${pid}/manage`} className="text-primary hover:underline">Firma hinzufügen</Link>
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
              await calculateValues(drawer.companyId, period.value, period.year);
              await loadAllValues();
              setDrawerOpen(false);
            }}
          />
        )}

        {tooltip && (() => {
          const cv = getVal(tooltip.companyId, tooltip.key);
          const def = definitions.find((d) => d.key === tooltip.key);
          if (!cv || !def) return null;

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
