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
  type ValueDefinition,
  type CompanyValue,
  type RefreshStatus,
} from "@/api/values";
import { AnalysisDrawer } from "@/components/AnalysisDrawer";
import { RefreshProgressBar } from "@/components/RefreshProgressBar";

const CATEGORY_ORDER = [
  "TRANSACTION", "BASIC_COMPANY", "HOHN_BASIC_1", "HOHN_BASIC_2",
  "VALUATION_ADJ", "RISK_ADJ", "MGMT_ADJ", "TOTAL_ADJ",
];

const CATEGORY_LABELS: Record<string, string> = {
  TRANSACTION: "Transaction Data",
  BASIC_COMPANY: "Basic Company Data",
  HOHN_BASIC_1: "Hohn Return (Basic 1)",
  HOHN_BASIC_2: "Hohn Return (Basic 2)",
  VALUATION_ADJ: "Valuation Adjustments",
  RISK_ADJ: "Risk Adjustments",
  MGMT_ADJ: "Management Adjustments",
  TOTAL_ADJ: "Total Adjustments",
};

const CATEGORY_COLORS: Record<string, string> = {
  TRANSACTION: "bg-slate-100 text-slate-700 border-slate-200",
  BASIC_COMPANY: "bg-blue-50 text-blue-700 border-blue-200",
  HOHN_BASIC_1: "bg-sky-50 text-sky-700 border-sky-200",
  HOHN_BASIC_2: "bg-teal-50 text-teal-700 border-teal-200",
  VALUATION_ADJ: "bg-amber-50 text-amber-700 border-amber-200",
  RISK_ADJ: "bg-rose-50 text-rose-700 border-rose-200",
  MGMT_ADJ: "bg-violet-50 text-violet-700 border-violet-200",
  TOTAL_ADJ: "bg-sky-100 text-sky-800 border-sky-300",
};

const PERIOD_OPTIONS = [
  { label: "Snapshot", value: "SNAPSHOT", year: undefined },
  { label: "LTM", value: "LTM", year: undefined },
  { label: "TTM", value: "TTM", year: undefined },
  { label: "FY 2025", value: "FY", year: 2025 },
  { label: "FY 2024", value: "FY", year: 2024 },
  { label: "FY 2023", value: "FY", year: 2023 },
  { label: "FY 2022", value: "FY", year: 2022 },
  { label: "FY 2021", value: "FY", year: 2021 },
];

const FX_RATES: Record<string, number> = { USD: 1, EUR: 0.92, GBP: 0.79, CHF: 0.88 };
const CURRENCIES = ["USD", "EUR", "GBP", "CHF"];

const FORMULAS: Record<string, string> = {
  net_debt: "Debt − Cash",
  eps_growth: "(EPS Forward − EPS TTM) / |EPS TTM| × 100",
  buyback_return: "|Buybacks| / Market Cap × 100",
  hohn_rendite_basic_1: "Dividend Return + Buyback Return + EPS Growth",
  fcf_yield: "Free Cash Flow / Market Cap × 100",
  hohn_rendite_basic_2: "FCF Yield + EPS Growth",
  pe_target_analysts: "Analysts Target / EPS Forward",
  upside_potential: "(Analysts Target − Stock Price) / Stock Price × 100",
  risk_factor: "Avg(Business Model, Regulatory, Macro)",
  mgmt_factor: "Avg(Participation)",
  total_adjustment_factor: "Risk Factor × Mgmt Factor",
  hohn_rendite_adjusted: "Hohn Return (Basic 1) × Total Adjustment Factor",
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
    isQualitative: boolean;
    dataType: string;
  } | null>(null);
  const [drawerOpen, setDrawerOpen] = useState(false);
  const [refreshStatuses, setRefreshStatuses] = useState<Map<string, RefreshStatus>>(new Map());

  const period = PERIOD_OPTIONS[periodIdx];

  const loadAllValues = useCallback(async () => {
    if (!pid || companies.length === 0) return;
    const qualitativeKeys = new Set(
      definitions.filter((d) => d.source_type === "QUALITATIVE").map((d) => d.key)
    );
    const map = new Map<string, CompanyValue[]>();
    await Promise.all(
      companies.map(async (c) => {
        const periodVals = await getCompanyValues(c.id, period.value, period.year);
        if (period.value !== "SNAPSHOT") {
          const snapshotVals = await getCompanyValues(c.id, "SNAPSHOT");
          const periodKeys = new Set(periodVals.map((v) => v.value_key));
          const qualVals = snapshotVals.filter((v) => qualitativeKeys.has(v.value_key) && !periodKeys.has(v.value_key));
          map.set(c.id, [...periodVals, ...qualVals]);
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
    setRefreshStatuses(new Map(entries));
  }, []);

  useEffect(() => {
    getValueDefinitions().then(setDefinitions);
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
    const f = FX_RATES[from] ?? 1;
    const t = FX_RATES[displayCurrency] ?? 1;
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
              <span className="text-xs text-muted-foreground">(Richtwerte, nicht live)</span>
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
                    <div>
                      <span>{company.name}</span>
                      <span className="ml-2 rounded bg-primary/10 px-1.5 py-0.5 font-mono text-[10px] font-medium text-primary">
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
                      const shouldConvert = d.unit !== "%" && d.data_type === "NUMERIC" && cv?.currency;
                      const displayVal = shouldConvert ? convertCurrency(rawValid, cv?.currency ?? null) : rawValid;
                      const isQualitative = d.source_type === "QUALITATIVE";

                      const isHistoricalQual = isQualitative && period.value !== "SNAPSHOT";

                      const isEditing = editCell?.companyId === company.id && editCell?.key === d.key;

                      const handleSaveEdit = async () => {
                        if (!editCell || saving) return;
                        const num = parseFloat(editCell.value);
                        if (isNaN(num)) { setEditCell(null); return; }
                        setSaving(true);
                        try {
                          await overrideValue(company.id, d.key, { numeric_value: num, source_name: "Manuell" });
                          const updated = await getCompanyValues(company.id, period.value, period.year);
                          setValuesMap((prev) => { const n = new Map(prev); n.set(company.id, updated); return n; });
                        } finally {
                          setSaving(false);
                          setEditCell(null);
                        }
                      };

                      const isEmpty = !cv || (cv.numeric_value == null && cv.text_value == null);
                      const isFromYahoo = cv?.source_name === "Yahoo Finance";
                      const canChat = isQualitative || isEmpty || !isFromYahoo;

                      return (
                        <td key={`${company.id}-${d.key}`}
                          className={`whitespace-nowrap border-r border-border/40 px-3 py-2 tabular ${canChat ? "cursor-pointer hover:bg-muted/30" : "cursor-text"} ${isHistoricalQual ? "bg-amber-50/50" : ""}`}
                          onClick={canChat ? () => {
                            setDrawer({
                              companyId: company.id,
                              valueKey: d.key,
                              companyName: company.name,
                              valueLabel: d.label_en,
                              currentScore: raw,
                              isQualitative,
                              dataType: d.data_type,
                            });
                            setDrawerOpen(true);
                          } : undefined}
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
                            <span className="font-mono text-sm text-foreground">
                              {d.data_type === "TEXT"
                                ? cv?.text_value ?? (cv?.numeric_value != null ? parseFloat(String(cv.numeric_value)).toFixed(2) : t.noValue)
                                : d.data_type === "FACTOR"
                                ? cv?.numeric_value != null ? parseFloat(String(cv.numeric_value)).toFixed(2) : (cv?.text_value ?? t.noValue)
                                : formatValue(displayVal, d.unit, displayCurrency)}
                            </span>
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
            dataType={drawer.dataType as "NUMERIC" | "TEXT" | "FACTOR"}
            periodType={period.value}
            periodYear={period.year}
            onAcceptScore={async (score, textValue) => {
              if (textValue !== undefined) {
                await overrideValue(drawer.companyId, drawer.valueKey, { text_value: textValue, source_name: "Claude Analysis" });
              } else if (score != null) {
                await overrideValue(drawer.companyId, drawer.valueKey, { numeric_value: score, source_name: "Claude Analysis" });
              }
              const updated = await getCompanyValues(drawer.companyId, period.value, period.year);
              setValuesMap((prev) => {
                const next = new Map(prev);
                next.set(drawer.companyId, updated);
                return next;
              });
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
