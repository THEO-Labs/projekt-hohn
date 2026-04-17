import { useEffect, useState, useCallback } from "react";
import { Link, useParams } from "react-router-dom";
import { ChevronLeft, RefreshCw, Info, X, ShieldCheck, Calculator, MessageSquare, Pencil } from "lucide-react";
import { createPortal } from "react-dom";
import { AppHeader } from "@/components/AppHeader";
import { Button } from "@/components/ui/button";
import { useAuth } from "@/hooks/useAuth";
import { t } from "@/lib/i18n";
import { formatValue } from "@/lib/format";
import {
  getValueDefinitions,
  getCompanyValues,
  refreshValues,
  type ValueDefinition,
  type CompanyValue,
} from "@/api/values";

const CATEGORY_ORDER = [
  "TRANSACTION",
  "BASIC_COMPANY",
  "HOHN_BASIC_1",
  "HOHN_BASIC_2",
  "VALUATION_ADJ",
  "RISK_ADJ",
  "MGMT_ADJ",
  "TOTAL_ADJ",
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
  { label: "FY 2026", value: "FY", year: 2026 },
  { label: "FY 2025", value: "FY", year: 2025 },
  { label: "FY 2024", value: "FY", year: 2024 },
  { label: "FY 2023", value: "FY", year: 2023 },
  { label: "FY 2022", value: "FY", year: 2022 },
  { label: "FY 2021", value: "FY", year: 2021 },
  { label: "FY 2020", value: "FY", year: 2020 },
];

const FX_RATES: Record<string, number> = {
  USD: 1,
  EUR: 0.92,
  GBP: 0.79,
  CHF: 0.88,
};

const CURRENCIES = ["USD", "EUR", "GBP", "CHF"];

export function CompanyDashboardPage() {
  const { pid, cid } = useParams<{ pid: string; cid: string }>();
  const { user, logout } = useAuth();

  const [definitions, setDefinitions] = useState<ValueDefinition[]>([]);
  const [values, setValues] = useState<CompanyValue[]>([]);
  const [periodIdx, setPeriodIdx] = useState(0);
  const [displayCurrency, setDisplayCurrency] = useState("USD");
  const [loadingKeys, setLoadingKeys] = useState<Set<string>>(new Set());
  const [tooltip, setTooltip] = useState<{ key: string; x: number; y: number } | null>(null);

  const period = PERIOD_OPTIONS[periodIdx];

  const loadValues = useCallback(() => {
    if (!cid) return;
    getCompanyValues(cid, period.value, period.year).then(setValues);
  }, [cid, period.value, period.year]);

  useEffect(() => {
    getValueDefinitions().then(setDefinitions);
  }, []);

  useEffect(() => {
    loadValues();
  }, [loadValues]);

  const handleRefresh = async (keys: string[]) => {
    if (!cid) return;
    setLoadingKeys((prev) => new Set([...prev, ...keys]));
    try {
      const updated = await refreshValues(cid, keys, period.value, period.year);
      setValues((prev) => {
        const map = new Map(prev.map((v) => [`${v.value_key}:${v.period_type}:${v.period_year}`, v]));
        for (const u of updated) {
          map.set(`${u.value_key}:${u.period_type}:${u.period_year}`, u);
        }
        return Array.from(map.values());
      });
    } finally {
      setLoadingKeys((prev) => {
        const next = new Set(prev);
        for (const k of keys) next.delete(k);
        return next;
      });
    }
  };

  const handleRefreshAll = () => {
    const apiKeys = definitions
      .filter((d) => d.source_type === "API")
      .map((d) => d.key);
    handleRefresh(apiKeys);
  };

  const getValueForKey = (key: string): CompanyValue | undefined =>
    values.find((v) => v.value_key === key);

  const convertCurrency = (val: number | null, fromCurrency: string | null): number | null => {
    if (val == null || !fromCurrency) return val;
    const from = FX_RATES[fromCurrency] ?? 1;
    const to = FX_RATES[displayCurrency] ?? 1;
    if (from === to) return val;
    return (val / from) * to;
  };

  const grouped = CATEGORY_ORDER.map((cat) => ({
    category: cat,
    label: CATEGORY_LABELS[cat],
    defs: definitions
      .filter((d) => d.category === cat)
      .sort((a, b) => a.sort_order - b.sort_order),
  })).filter((g) => g.defs.length > 0);

  if (!user) return null;

  return (
    <div className="min-h-screen bg-background">
      <AppHeader email={user.email} onLogout={logout} />
      <main className="p-6">
        <div className="mb-4">
          <Link
            to={`/portfolios/${pid}`}
            className="inline-flex items-center gap-1 text-sm text-muted-foreground transition-colors hover:text-foreground"
          >
            <ChevronLeft className="h-3.5 w-3.5" />
            {t.back}
          </Link>
        </div>

        <div className="mb-6 flex flex-wrap items-end justify-between gap-4">
          <h2 className="text-2xl font-semibold tracking-tight text-foreground">
            {t.dashboard}
          </h2>
          <div className="flex items-center gap-3">
            <Button variant="outline" size="sm" onClick={handleRefreshAll}>
              <RefreshCw className="mr-1.5 h-3.5 w-3.5" />
              {t.allValues}
            </Button>
            <select
              value={periodIdx}
              onChange={(e) => setPeriodIdx(Number(e.target.value))}
              className="rounded-md border border-input bg-background px-3 py-1.5 text-sm text-foreground"
            >
              {PERIOD_OPTIONS.map((p, i) => (
                <option key={i} value={i}>
                  {p.label}
                </option>
              ))}
            </select>
            <select
              value={displayCurrency}
              onChange={(e) => setDisplayCurrency(e.target.value)}
              className="rounded-md border border-input bg-background px-3 py-1.5 text-sm text-foreground"
            >
              {CURRENCIES.map((c) => (
                <option key={c} value={c}>
                  {c}
                </option>
              ))}
            </select>
          </div>
        </div>

        <div className="overflow-x-auto rounded-xl border border-border/60 bg-card">
          <table className="w-full text-sm">
            <thead>
              <tr>
                {grouped.map((g) => (
                  <th
                    key={g.category}
                    colSpan={g.defs.length}
                    className={`border-b border-r px-3 py-2 text-center text-xs font-semibold uppercase tracking-wider ${CATEGORY_COLORS[g.category]}`}
                  >
                    {g.label}
                  </th>
                ))}
              </tr>
              <tr>
                {grouped.flatMap((g) =>
                  g.defs.map((d) => (
                    <th
                      key={d.key}
                      className="whitespace-nowrap border-b border-r border-border/40 px-3 py-2 text-left text-xs font-medium text-muted-foreground"
                    >
                      <div className="flex items-center gap-1">
                        <span className="truncate" title={d.label_de}>
                          {d.label_en}
                        </span>
                        {d.source_type === "API" && (
                          <button
                            onClick={() => handleRefresh([d.key])}
                            disabled={loadingKeys.has(d.key)}
                            className="ml-auto shrink-0 rounded p-0.5 text-muted-foreground transition-colors hover:bg-muted hover:text-foreground disabled:opacity-40"
                            title={t.calculate}
                          >
                            <RefreshCw
                              className={`h-3 w-3 ${loadingKeys.has(d.key) ? "animate-spin" : ""}`}
                            />
                          </button>
                        )}
                      </div>
                    </th>
                  ))
                )}
              </tr>
            </thead>
            <tbody>
              <tr>
                {grouped.flatMap((g) =>
                  g.defs.map((d) => {
                    const cv = getValueForKey(d.key);
                    const raw = cv?.numeric_value ?? null;
                    const shouldConvert = d.unit !== "%" && d.data_type === "NUMERIC" && cv?.currency;
                    const displayVal = shouldConvert
                      ? convertCurrency(raw, cv?.currency ?? null)
                      : raw;

                    return (
                      <td
                        key={d.key}
                        className="whitespace-nowrap border-r border-border/40 px-3 py-2 tabular"
                      >
                        <div className="flex items-center gap-1.5">
                          <span className="font-mono text-sm text-foreground">
                            {d.data_type === "TEXT" || d.data_type === "FACTOR"
                              ? cv?.text_value ?? cv?.numeric_value?.toString() ?? t.noValue
                              : formatValue(displayVal, d.unit, displayCurrency)}
                          </span>
                          {cv && (
                            <button
                              onClick={(e) => {
                                const rect = (e.target as HTMLElement).getBoundingClientRect();
                                setTooltip(tooltip?.key === d.key ? null : { key: d.key, x: rect.left, y: rect.bottom + 6 });
                              }}
                              className="shrink-0 rounded p-0.5 text-muted-foreground/50 transition-colors hover:text-muted-foreground"
                            >
                              <Info className="h-3 w-3" />
                            </button>
                          )}
                        </div>
                      </td>
                    );
                  })
                )}
              </tr>
            </tbody>
          </table>
        </div>

        {tooltip && (() => {
          const cv = getValueForKey(tooltip.key);
          const def = definitions.find((d) => d.key === tooltip.key);
          if (!cv || !def) return null;

          const confidence = cv.manually_overridden
            ? { label: "Manuell überschrieben", color: "bg-amber-100 text-amber-800 border-amber-300", icon: Pencil }
            : def.source_type === "API"
            ? { label: "Hohe Sicherheit (API)", color: "bg-green-100 text-green-800 border-green-300", icon: ShieldCheck }
            : def.source_type === "CALCULATED"
            ? { label: "Berechnet", color: "bg-blue-100 text-blue-800 border-blue-300", icon: Calculator }
            : def.source_type === "QUALITATIVE"
            ? { label: "Qualitativ (Einschätzung)", color: "bg-amber-100 text-amber-800 border-amber-300", icon: MessageSquare }
            : { label: "Nutzereingabe", color: "bg-slate-100 text-slate-700 border-slate-300", icon: Pencil };

          const ConfIcon = confidence.icon;

          return createPortal(
            <>
              <div className="fixed inset-0 z-[99]" onClick={() => setTooltip(null)} />
              <div
                className="fixed z-[100] w-72 rounded-xl border border-border bg-card p-4 shadow-2xl shadow-black/10"
                style={{ left: Math.min(tooltip.x, window.innerWidth - 300), top: Math.min(tooltip.y, window.innerHeight - 250) }}
              >
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
                          {new URL(cv.source_link).hostname}
                        </a>
                      </dd>
                    </div>
                  )}
                  <div className="flex justify-between">
                    <dt className="text-muted-foreground">{t.fetchedAt}</dt>
                    <dd className="text-foreground">
                      {cv.fetched_at ? new Date(cv.fetched_at).toLocaleString("de-DE") : "—"}
                    </dd>
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
