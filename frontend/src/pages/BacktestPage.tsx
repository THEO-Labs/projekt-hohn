import { useEffect, useState, useCallback } from "react";
import { Link, useParams } from "react-router-dom";
import { ChevronLeft, RefreshCw, AlertTriangle } from "lucide-react";

import { AppHeader } from "@/components/AppHeader";
import { useAuth } from "@/hooks/useAuth";
import { listCompanies, type Company } from "@/api/companies";
import {
  getCumulativeValues,
  getStockReturn,
  fetchHistoricalStammdaten,
  type CumulativeValuesResponse,
  type StockReturnResponse,
} from "@/api/values";

const STICHTAGE: { label: string; year: number; date: string }[] = [
  { label: "seit 2024", year: 2024, date: "2024-01-02" },
  { label: "seit 2023", year: 2023, date: "2023-01-02" },
  { label: "seit 2022", year: 2022, date: "2022-01-03" },
  { label: "seit 2021", year: 2021, date: "2021-01-04" },
  { label: "seit 2020", year: 2020, date: "2020-01-02" },
];

const TO_YEAR = 2025;

type RowData = {
  company: Company;
  cum: CumulativeValuesResponse | null;
  stockReturn: StockReturnResponse | null;
  loading: boolean;
};

function fmt(n: number | null | undefined, suffix = "%"): string {
  if (n == null || isNaN(n)) return "—";
  return `${n.toFixed(2)} ${suffix}`;
}

function colorFor(value: number | null | undefined, thresholds: [number, number, number, number]): string {
  if (value == null || isNaN(value)) return "";
  const [exc, good, weak, bad] = thresholds;
  if (value >= exc) return "bg-emerald-100/70";
  if (value >= good) return "bg-green-50";
  if (value >= weak) return "";
  if (value >= bad) return "bg-orange-50";
  return "bg-red-100/60";
}

export function BacktestPage() {
  const { pid } = useParams<{ pid: string }>();
  const { user, logout } = useAuth();
  const [companies, setCompanies] = useState<Company[]>([]);
  const [selectedStichtag, setSelectedStichtag] = useState(STICHTAGE[2]);
  const [rows, setRows] = useState<Map<string, RowData>>(new Map());
  const [loadingAll, setLoadingAll] = useState(false);

  useEffect(() => {
    if (pid) listCompanies(pid).then(setCompanies);
  }, [pid]);

  const loadData = useCallback(async () => {
    if (companies.length === 0) return;
    setLoadingAll(true);
    const next = new Map<string, RowData>();
    companies.forEach((c) => next.set(c.id, { company: c, cum: null, stockReturn: null, loading: true }));
    setRows(next);

    await Promise.all(
      companies.map(async (c) => {
        try {
          await fetchHistoricalStammdaten(c.id, selectedStichtag.year);
        } catch {
          // ignore — auto-fetch best-effort
        }
        const [cum, sr] = await Promise.all([
          getCumulativeValues(c.id, selectedStichtag.year, TO_YEAR).catch(() => null),
          getStockReturn(c.id, selectedStichtag.date).catch(() => null),
        ]);
        setRows((prev) => {
          const upd = new Map(prev);
          upd.set(c.id, { company: c, cum, stockReturn: sr, loading: false });
          return upd;
        });
      })
    );
    setLoadingAll(false);
  }, [companies, selectedStichtag]);

  useEffect(() => {
    loadData();
  }, [loadData]);

  if (!user) return null;

  return (
    <div className="min-h-screen bg-background">
      <AppHeader email={user.email} onLogout={logout} />
      <main className="p-6">
        <div className="mb-4">
          <Link to={`/portfolios/${pid}`} className="inline-flex items-center gap-1 text-sm text-muted-foreground transition-colors hover:text-foreground">
            <ChevronLeft className="h-3.5 w-3.5" />
            Zurück zum Portfolio
          </Link>
        </div>

        <div className="mb-4 flex flex-wrap items-end justify-between gap-4">
          <div>
            <h2 className="text-2xl font-semibold tracking-tight text-foreground">Backtest — Hohn-Rendite vs. realisierte Stock-Performance</h2>
            <p className="mt-1 text-xs text-muted-foreground">
              Erwarteter Return (Hohn-Rendite kumuliert, p.a.) gegen tatsächlichen Stock-Total-Return (Adj Close, CAGR).
            </p>
          </div>
          <div className="flex items-center gap-2">
            <span className="text-[10px] font-semibold uppercase tracking-wide text-muted-foreground">Stichtag</span>
            <div className="flex items-center gap-1">
              {STICHTAGE.map((s) => (
                <button key={s.year}
                  onClick={() => setSelectedStichtag(s)}
                  className={`rounded-md px-2 py-1 text-[11px] font-medium transition-colors ${selectedStichtag.year === s.year ? "bg-primary text-primary-foreground" : "border border-border bg-background text-foreground hover:bg-muted"}`}
                >{s.label}</button>
              ))}
            </div>
            <button onClick={loadData} disabled={loadingAll}
              className="ml-2 flex items-center gap-1 rounded-md border border-border bg-background px-2 py-1 text-[11px] font-medium hover:bg-muted disabled:opacity-50">
              <RefreshCw className={`h-3 w-3 ${loadingAll ? "animate-spin" : ""}`} />
              Reload
            </button>
          </div>
        </div>

        <div className="mb-4 rounded-lg border border-border/60 bg-muted/30 px-4 py-2.5">
          <div className="text-xs text-muted-foreground">
            Periode: {selectedStichtag.date} → heute · Hohn-Rendite kumuliert über {TO_YEAR - selectedStichtag.year + 1} FYs ({selectedStichtag.year}–{TO_YEAR})
            mit MCap-Stichtag heute · Stock-CAGR aus Adj Close inkl. Dividenden + Splits
          </div>
        </div>

        <div className="overflow-x-auto rounded-xl border border-border/60 bg-card">
          <table className="w-full text-sm">
            <thead>
              <tr className="border-b border-border/40 bg-muted/40 text-xs uppercase text-muted-foreground">
                <th className="sticky left-0 bg-muted/40 px-3 py-2 text-left">Firma</th>
                <th className="px-3 py-2 text-right">Hohn (simple, p.a.)</th>
                <th className="px-3 py-2 text-right">Hohn (detailed, p.a.)</th>
                <th className="px-3 py-2 text-right">Stock-CAGR (real.)</th>
                <th className="px-3 py-2 text-right">Stock TR total</th>
                <th className="px-3 py-2 text-right">Δ (real. − Hohn simple)</th>
              </tr>
            </thead>
            <tbody>
              {companies.map((c) => {
                const row = rows.get(c.id);
                const cum = row?.cum;
                const sr = row?.stockReturn;
                const hohnSimple = cum?.values?.hohn_return_simple?.pa_avg ?? null;
                const hohnDetailed = cum?.values?.hohn_return_detailed?.pa_avg ?? null;
                const hsNum = hohnSimple != null ? parseFloat(hohnSimple) : null;
                const hdNum = hohnDetailed != null ? parseFloat(hohnDetailed) : null;
                const cagrNum = sr?.cagr_pct ?? null;
                const trNum = sr?.total_return_pct ?? null;
                const diff = cagrNum != null && hsNum != null ? cagrNum - hsNum : null;
                return (
                  <tr key={c.id} className="border-b border-border/30 last:border-b-0 hover:bg-muted/10">
                    <td className="sticky left-0 bg-card px-3 py-2 font-medium text-foreground">
                      <div className="flex items-center gap-2">
                        <span>{c.name}</span>
                        <span className="rounded bg-primary/10 px-1.5 py-0.5 font-mono text-[10px] font-medium text-primary">
                          {c.ticker}
                        </span>
                        {row?.loading && <RefreshCw className="h-3 w-3 animate-spin text-muted-foreground" />}
                      </div>
                    </td>
                    <td className={`px-3 py-2 text-right font-mono ${colorFor(hsNum, [15, 10, 5, 0])}`}>
                      {fmt(hsNum, "% p.a.")}
                      {(cum?.values?.hohn_return_simple?.missing?.length ?? 0) > 0 && (
                        <AlertTriangle className="ml-1 inline-block h-3 w-3 text-amber-600"
                          aria-label={`Partial: ${cum?.values?.hohn_return_simple?.missing?.join(", ")}`} />
                      )}
                    </td>
                    <td className={`px-3 py-2 text-right font-mono ${colorFor(hdNum, [15, 10, 5, 0])}`}>
                      {fmt(hdNum, "% p.a.")}
                    </td>
                    <td className={`px-3 py-2 text-right font-mono ${colorFor(cagrNum, [15, 10, 5, 0])}`}>
                      {fmt(cagrNum, "% p.a.")}
                    </td>
                    <td className="px-3 py-2 text-right font-mono text-foreground">
                      {fmt(trNum)}
                    </td>
                    <td className={`px-3 py-2 text-right font-mono font-semibold ${diff != null && diff > 0 ? "text-emerald-700" : diff != null && diff < 0 ? "text-red-700" : "text-muted-foreground"}`}>
                      {diff != null ? `${diff > 0 ? "+" : ""}${diff.toFixed(2)} pp` : "—"}
                    </td>
                  </tr>
                );
              })}
              {companies.length === 0 && (
                <tr>
                  <td colSpan={6} className="px-6 py-12 text-center text-sm text-muted-foreground">
                    Keine Firmen im Portfolio.
                  </td>
                </tr>
              )}
            </tbody>
          </table>
        </div>

        <div className="mt-4 grid grid-cols-2 gap-3 text-[11px] text-muted-foreground">
          <div className="rounded-lg border border-border/40 bg-muted/20 px-3 py-2">
            <div className="font-semibold text-foreground">Hypothesen-Lesart</div>
            <p className="mt-1">
              Δ &gt; 0 → Stock hat MEHR realisiert als Hohn-Rendite vorhergesagt (Markt hat Quality "noch teurer" gemacht / Multiple-Expansion).
              Δ &lt; 0 → Stock hat WENIGER realisiert (Multiple-Compression oder schwächere Performance als Fundamentals nahelegten).
            </p>
          </div>
          <div className="rounded-lg border border-border/40 bg-muted/20 px-3 py-2">
            <div className="font-semibold text-foreground">Methodik</div>
            <p className="mt-1">
              Hohn p.a. = kumulierte Σ Cashflows / heutiger MCap, geteilt durch Anzahl FYs (avg).
              Stock-CAGR = (AdjClose heute / AdjClose Stichtag)^(1/Jahre) − 1. Adj Close enthält Dividenden + Splits.
            </p>
          </div>
        </div>
      </main>
    </div>
  );
}
