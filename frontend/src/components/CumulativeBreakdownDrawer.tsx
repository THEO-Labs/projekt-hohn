import { useEffect } from "react";
import { createPortal } from "react-dom";
import { X, AlertTriangle, Calculator } from "lucide-react";
import type { CumulativeValuesResponse } from "@/api/values";

const FACTOR_INPUTS: Record<string, { inputs: string[]; formula: string }> = {
  fcf_yield: { inputs: ["fcf"], formula: "Σ FCF / aktueller MCap" },
  sbc_yield: { inputs: ["sbc"], formula: "Σ SBC / aktueller MCap" },
  dividend_yield: { inputs: ["dividends"], formula: "Σ Dividends / aktueller MCap" },
  buyback_yield: { inputs: ["buyback_volume"], formula: "Σ Buyback Volume / aktueller MCap" },
  net_buyback_yield: { inputs: ["buyback_volume", "sbc"], formula: "(Σ Buyback Volume − Σ SBC) / aktueller MCap" },
  ni_growth: { inputs: ["net_income"], formula: "(NI[end] / NI[pre]) − 1, plus annualisiert via CAGR" },
  net_debt_change_pct: {
    inputs: ["net_debt", "lease_liabilities", "long_term_debt", "cash_and_equivalents", "marketable_securities_st", "marketable_securities_lt"],
    formula: "(Net Debt[pre] − Net Debt[end]) / aktueller MCap",
  },
  hohn_return_simple: {
    inputs: [],
    formula: "FCF Yield + NI Growth − SBC Yield + ΔND/MCap (alle kumuliert)",
  },
  hohn_return_detailed: {
    inputs: [],
    formula: "Dividend Yield + NI Growth + Net Buyback Yield + ΔND/MCap (alle kumuliert)",
  },
};

const KEY_LABELS: Record<string, string> = {
  fcf: "Free Cash Flow",
  sbc: "Stock Based Compensation",
  dividends: "Dividends",
  buyback_volume: "Buyback Volume",
  net_income: "Net Income",
  net_debt: "Net Debt",
  lease_liabilities: "Lease Liabilities",
  long_term_debt: "Long-term Debt",
  cash_and_equivalents: "Cash & Equivalents",
  marketable_securities_st: "Marketable Securities (ST)",
  marketable_securities_lt: "Marketable Securities (LT)",
  debt_sum: "Debt Sum",
  cash_sum: "Cash Sum",
};

function formatBig(raw: string | null): string {
  if (raw == null) return "—";
  const n = parseFloat(raw);
  if (isNaN(n)) return "—";
  const abs = Math.abs(n);
  if (abs >= 1e9) return `${(n / 1e9).toFixed(2)} B`;
  if (abs >= 1e6) return `${(n / 1e6).toFixed(2)} M`;
  if (abs >= 1e3) return `${(n / 1e3).toFixed(2)} K`;
  return n.toFixed(2);
}

function formatPct(raw: string | null, suffix = "%"): string {
  if (raw == null) return "—";
  const n = parseFloat(raw);
  if (isNaN(n)) return "—";
  return `${n.toFixed(2)} ${suffix}`;
}

type Props = {
  open: boolean;
  onClose: () => void;
  companyName: string;
  valueKey: string;
  valueLabel: string;
  cum: CumulativeValuesResponse;
  onJumpToFy?: (year: number) => void;
};

export function CumulativeBreakdownDrawer({
  open,
  onClose,
  companyName,
  valueKey,
  valueLabel,
  cum,
  onJumpToFy,
}: Props) {
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && onClose();
    if (open) window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [open, onClose]);

  const cell = cum.values[valueKey];
  const meta = FACTOR_INPUTS[valueKey] ?? { inputs: [], formula: "" };
  const years = Object.keys(cum.per_year_breakdown).map(Number).sort();

  return createPortal(
    <div className={`fixed inset-0 z-[200] flex justify-end transition-opacity duration-200 ${open ? "opacity-100 pointer-events-auto" : "opacity-0 pointer-events-none"}`}>
      <div className="absolute inset-0 bg-black/30" onClick={onClose} />
      <div className={`relative flex h-full w-full flex-col bg-card shadow-2xl transition-transform duration-300 sm:w-[560px] ${open ? "translate-x-0" : "translate-x-full"}`}>
        <header className="flex shrink-0 items-start justify-between border-b border-border px-5 py-4">
          <div>
            <div className="flex items-center gap-2">
              <Calculator className="h-4 w-4 text-primary" />
              <span className="text-sm font-semibold text-foreground">{valueLabel}</span>
            </div>
            <p className="mt-0.5 text-xs text-muted-foreground">{companyName}</p>
            <p className="mt-1 text-[11px] text-muted-foreground italic">{meta.formula}</p>
          </div>
          <button onClick={onClose} className="rounded-md p-1.5 text-muted-foreground transition-colors hover:bg-muted hover:text-foreground">
            <X className="h-4 w-4" />
          </button>
        </header>

        <div className="shrink-0 border-b border-border bg-muted/30 px-5 py-4">
          <div className="grid grid-cols-2 gap-3 text-center">
            <div>
              <div className="text-[10px] uppercase tracking-wide text-muted-foreground">Kumuliert</div>
              <div className="mt-1 font-mono text-lg font-semibold text-foreground">{formatPct(cell?.cum)}</div>
            </div>
            <div>
              <div className="text-[10px] uppercase tracking-wide text-muted-foreground">p.a. (Durchschnitt)</div>
              <div className="mt-1 font-mono text-lg font-semibold text-foreground">{formatPct(cell?.pa_avg, "% p.a.")}</div>
            </div>
          </div>
          <div className="mt-2 text-center text-[11px] text-muted-foreground">
            Periode: {years[0] ?? cum.from_year}-{years[years.length - 1] ?? cum.to_year} ({years.length} FYs) · MCap heute: {formatBig(cum.market_cap)}
          </div>
        </div>

        {cell?.missing && cell.missing.length > 0 && (
          <div className="shrink-0 border-b border-border bg-amber-50 px-5 py-3">
            <div className="flex items-start gap-2">
              <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0 text-amber-600" />
              <div>
                <div className="text-xs font-semibold text-amber-900">Daten unvollständig — Wert ist partial oder leer.</div>
                <ul className="mt-1 list-disc pl-4 text-[11px] text-amber-900/80 space-y-0.5">
                  {cell.missing.map((m, i) => <li key={i}>{m}</li>)}
                </ul>
              </div>
            </div>
          </div>
        )}

        <div className="flex-1 overflow-y-auto px-5 py-4">
          {meta.inputs.length === 0 ? (
            <div className="text-xs text-muted-foreground">
              Aggregat-Wert. Komponenten siehe in den jeweiligen Faktor-Spalten.
            </div>
          ) : (
            <>
              <h3 className="mb-3 text-xs font-semibold uppercase tracking-wide text-muted-foreground">
                Per-FY Aufschlüsselung
              </h3>
              <table className="w-full text-xs">
                <thead>
                  <tr className="border-b border-border text-muted-foreground">
                    <th className="px-2 py-1.5 text-left">FY</th>
                    {meta.inputs.map((inp) => (
                      <th key={inp} className="px-2 py-1.5 text-right font-mono">
                        {KEY_LABELS[inp] ?? inp}
                      </th>
                    ))}
                  </tr>
                </thead>
                <tbody>
                  {years.map((year) => {
                    const yearData = cum.per_year_breakdown[String(year)] ?? {};
                    return (
                      <tr key={year} className="border-b border-border/40">
                        <td className="px-2 py-1.5 font-medium text-foreground">
                          {onJumpToFy ? (
                            <button
                              onClick={() => { onJumpToFy(year); onClose(); }}
                              className="rounded text-foreground transition-colors hover:text-primary hover:underline"
                              title={`Zur FY${year} Detail-Ansicht wechseln`}
                            >
                              FY{year} →
                            </button>
                          ) : (
                            <>FY{year}</>
                          )}
                        </td>
                        {meta.inputs.map((inp) => (
                          <td key={inp} className="px-2 py-1.5 text-right font-mono text-foreground">
                            {formatBig(yearData[inp] ?? null)}
                          </td>
                        ))}
                      </tr>
                    );
                  })}
                  <tr className="bg-muted/40 font-semibold">
                    <td className="px-2 py-2 text-foreground">Σ</td>
                    {meta.inputs.map((inp) => {
                      const sum = years.reduce((acc, y) => {
                        const v = cum.per_year_breakdown[String(y)]?.[inp];
                        return v != null ? acc + parseFloat(v) : acc;
                      }, 0);
                      const allPresent = years.every((y) => cum.per_year_breakdown[String(y)]?.[inp] != null);
                      return (
                        <td key={inp} className="px-2 py-2 text-right font-mono text-foreground">
                          {allPresent ? formatBig(String(sum)) : "—"}
                        </td>
                      );
                    })}
                  </tr>
                </tbody>
              </table>

              <div className="mt-4 rounded-lg border border-border bg-muted/20 px-3 py-2 text-[11px] text-muted-foreground">
                <div className="font-semibold text-foreground">Pre-Period (FY{cum.pre_period_year}):</div>
                <div className="mt-1 grid grid-cols-2 gap-x-3 gap-y-0.5">
                  {Object.entries(cum.pre_period_breakdown).map(([k, v]) => (
                    <div key={k} className="flex justify-between">
                      <span className="truncate">{KEY_LABELS[k] ?? k}:</span>
                      <span className="ml-2 font-mono">{formatBig(v)}</span>
                    </div>
                  ))}
                </div>
                <div className="mt-1.5 italic">Pre-Period-Werte werden für NI Growth (start) und ΔNet Debt (start) verwendet.</div>
              </div>
            </>
          )}
        </div>

        <footer className="shrink-0 border-t border-border px-5 py-3">
          <p className="rounded-lg border border-border bg-muted/40 px-3 py-2 text-center text-[11px] text-muted-foreground">
            Read-only Aufschlüsselung. Für Chat zu einer einzelnen FY → Period auf "FY-Analyse" wechseln und das jeweilige Jahr auswählen.
          </p>
        </footer>
      </div>
    </div>,
    document.body
  );
}
