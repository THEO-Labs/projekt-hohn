import { useEffect, useRef, useState } from "react";
import { Link } from "react-router-dom";
import { MoreHorizontal, Trash2, Pencil } from "lucide-react";
import { toast } from "sonner";

import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import type { CompanyCardData, FyKpiKey } from "@/api/dashboard";
import { loadCompanyCard } from "@/api/dashboard";
import type { ValueDefinition } from "@/api/values";
import { deleteCompany, updateCompany } from "@/api/companies";
import {
  AgeBadge,
  OverviewRow,
  RefreshActions,
  hReturnTone,
} from "@/components/companyCardBits";
import {
  formatEarningsDate,
  isEarningsPast,
  formatFyEnd,
  formatNumber,
  formatPercent,
  formatShort,
  isEarningsSoon,
} from "@/lib/format";

// KPI-Bestandteile des H-Returns: Label und Formatierung je value_key
const KPI_ITEMS: { key: FyKpiKey; label: string; kind: "percent" | "ratio" }[] = [
  { key: "dividend_yield", label: "Dividend Yield", kind: "percent" },
  { key: "ni_growth", label: "NI Growth", kind: "percent" },
  { key: "net_buyback_yield", label: "Net Buyback Yield", kind: "percent" },
  { key: "net_debt_change_pct", label: "ΔNet Debt", kind: "percent" },
  { key: "pe_ratio", label: "PE Ratio", kind: "ratio" },
  { key: "h_peg", label: "PEG", kind: "ratio" },
  { key: "ps_ratio", label: "PS Ratio", kind: "ratio" },
  { key: "fcf_yield", label: "FCF Yield", kind: "percent" },
  { key: "net_debt_to_ocf", label: "Net Debt/Op. CF", kind: "ratio" },
];

function formatKpi(value: number | null, kind: "percent" | "ratio"): string {
  if (value == null || Number.isNaN(value)) return "—";
  if (kind === "percent") return formatPercent(value, 1);
  return formatNumber(value, Math.abs(value) >= 10 ? 1 : 2);
}

type Props = {
  portfolioId: string;
  data: CompanyCardData;
  definitions: ValueDefinition[];
  onChanged: (updated: CompanyCardData | null) => void;
};

function Menu({
  onEditFy,
  onDelete,
}: {
  onEditFy: () => void;
  onDelete: () => void;
}) {
  const [open, setOpen] = useState(false);
  const ref = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!open) return;
    const onClick = (e: MouseEvent) => {
      if (ref.current && !ref.current.contains(e.target as Node)) setOpen(false);
    };
    document.addEventListener("mousedown", onClick);
    return () => document.removeEventListener("mousedown", onClick);
  }, [open]);

  return (
    <div className="relative" ref={ref}>
      <Button variant="ghost" size="icon-xs" onClick={() => setOpen((v) => !v)} aria-label="Aktionen">
        <MoreHorizontal className="h-3.5 w-3.5" />
      </Button>
      {open && (
        <div className="absolute right-0 top-7 z-10 w-48 overflow-hidden rounded-lg border border-border/60 bg-popover shadow-lg">
          <button
            type="button"
            onClick={() => { setOpen(false); onEditFy(); }}
            className="flex w-full items-center gap-2 px-3 py-2 text-left text-sm hover:bg-muted"
          >
            <Pencil className="h-3.5 w-3.5 text-muted-foreground" />
            Edit fiscal year end
          </button>
          <button
            type="button"
            onClick={() => { setOpen(false); onDelete(); }}
            className="flex w-full items-center gap-2 px-3 py-2 text-left text-sm text-destructive hover:bg-destructive/10"
          >
            <Trash2 className="h-3.5 w-3.5" />
            Delete company
          </button>
        </div>
      )}
    </div>
  );
}

export function CompanyCard({ portfolioId, data, definitions, onChanged }: Props) {
  const { company } = data;
  const currency = company.currency;

  const [editingFy, setEditingFy] = useState(false);
  const [fyMonth, setFyMonth] = useState(company.fiscal_year_end_month ? String(company.fiscal_year_end_month) : "");
  const [fyDay, setFyDay] = useState(company.fiscal_year_end_day ? String(company.fiscal_year_end_day) : "");

  const onDelete = async () => {
    if (!window.confirm(`Delete ${company.name}?`)) return;
    try {
      await deleteCompany(company.id);
      onChanged(null);
      toast.success(`${company.ticker} deleted`);
    } catch {
      toast.error("Delete failed");
    }
  };

  const onSaveFy = async () => {
    const m = parseInt(fyMonth, 10);
    const d = parseInt(fyDay, 10);
    if (!m || m < 1 || m > 12 || !d || d < 1 || d > 31) {
      toast.error("Invalid date (month 1-12, day 1-31)");
      return;
    }
    try {
      await updateCompany(company.id, { fiscal_year_end_month: m, fiscal_year_end_day: d });
      setEditingFy(false);
      const fresh = await loadCompanyCard(company);
      onChanged(fresh);
      toast.success("Fiscal year end updated");
    } catch {
      toast.error("Save failed");
    }
  };

  const detailHref = `/portfolios/${portfolioId}/companies/${company.id}`;

  // Naechster Earnings-Termin: leer -> kein Badge; <= 7 Tage -> dezente Amber-Hervorhebung
  const earningsLabel = formatEarningsDate(company.next_earnings_date);
  const earningsSoon = isEarningsSoon(company.next_earnings_date);
  const earningsPast = isEarningsPast(company.next_earnings_date);

  return (
    <Card className="gap-0 py-0">
      {/* Header + Actions row */}
      <div className="flex items-center justify-between gap-3 border-b border-border/40 px-5 py-2">
        <Link to={detailHref} className="group/link flex min-w-0 flex-1 items-center gap-2.5">
          <div className="min-w-0">
            <div className="truncate text-[14px] font-semibold tracking-tight text-foreground transition-colors group-hover/link:text-primary">
              {company.name}
            </div>
          </div>
          <span className="rounded bg-primary/10 px-1.5 py-0.5 font-mono text-[10px] font-semibold text-primary">
            {company.ticker}
          </span>
          <span className="rounded bg-muted px-1.5 py-0.5 font-mono text-[10px] font-medium text-muted-foreground">
            {currency}
          </span>
          {data.fy_estimate_year && (
            <span className="rounded bg-violet-100 px-1.5 py-0.5 font-mono text-[10px] font-medium text-violet-700 ring-1 ring-violet-200">
              FY {data.fy_estimate_year}
            </span>
          )}
          {earningsLabel && (
            <span
              className={
                earningsSoon
                  ? "rounded bg-amber-100 px-1.5 py-0.5 font-mono text-[10px] font-medium text-amber-700 ring-1 ring-amber-200"
                  : earningsPast
                    ? "rounded bg-muted px-1.5 py-0.5 font-mono text-[10px] font-medium text-muted-foreground/50 line-through"
                    : "rounded bg-muted px-1.5 py-0.5 font-mono text-[10px] font-medium text-muted-foreground"
              }
              title={earningsPast
                ? "Letzter bekannter Earnings-Termin (bereits vorbei — Daily Refresh aktualisiert)"
                : "Naechster Earnings-Termin"}
            >
              Earnings: {earningsLabel}
            </span>
          )}
        </Link>
        <div className="flex items-center gap-1.5">
          <RefreshActions
            company={company}
            fyEstimateYear={data.fy_estimate_year}
            definitions={definitions}
            onRefreshed={(d) => onChanged(d)}
            variant="compact"
          />
          <Menu onEditFy={() => setEditingFy(true)} onDelete={onDelete} />
        </div>
      </div>

      {/* Body */}
      <div className="grid grid-cols-1 gap-x-10 gap-y-2 px-5 py-2.5 md:grid-cols-[minmax(0,1fr)_minmax(0,0.9fr)]">
        {/* Overview */}
        <div>
          <OverviewRow label="Stock price" value={formatShort(data.stock_price?.numeric_value ?? null, currency)} ts={data.stock_price?.fetched_at} />
          <OverviewRow label="Shares outstanding" value={formatShort(data.shares_outstanding?.numeric_value ?? null)} ts={data.shares_outstanding?.fetched_at} />
          <OverviewRow label="Market Cap" value={formatShort(data.market_cap?.numeric_value ?? null, currency)} ts={data.market_cap?.fetched_at} />
          <OverviewRow label="Enterprise Value" value={formatShort(data.enterprise_value, currency)} ts={data.enterprise_value_ts} />
          {editingFy ? (
            <div className="flex items-center gap-2 py-0.5">
              <div className="text-[12px] text-muted-foreground">Fiscal Year End</div>
              <Input value={fyDay} onChange={(e) => setFyDay(e.target.value)} placeholder="DD" className="h-6 w-12 text-center font-mono text-xs" />
              <span>.</span>
              <Input value={fyMonth} onChange={(e) => setFyMonth(e.target.value)} placeholder="MM" className="h-6 w-12 text-center font-mono text-xs" />
              <Button size="xs" variant="default" onClick={onSaveFy}>OK</Button>
              <Button size="xs" variant="ghost" onClick={() => setEditingFy(false)}>Cancel</Button>
            </div>
          ) : (
            <OverviewRow label="Fiscal Year End" value={formatFyEnd(company.fiscal_year_end_month, company.fiscal_year_end_day)} />
          )}
        </div>

        {/* H-Returns — kompakt, zwei Spalten nebeneinander */}
        <div className="border-t border-border/40 pt-2.5 md:border-l md:border-t-0 md:pl-8 md:pt-0">
          <div className="grid grid-cols-2 gap-6">
            <div>
              <div className="flex items-center justify-between gap-2">
                <div className="text-[10px] font-semibold tracking-wide text-muted-foreground uppercase">
                  H-Return {company.accounting_standard}
                </div>
                <AgeBadge iso={data.h_return_gaap?.fetched_at} />
              </div>
              <div className={`mt-0.5 text-2xl font-bold tabular-nums ${hReturnTone(data.h_return_gaap?.numeric_value ?? null)}`}>
                {formatPercent(data.h_return_gaap?.numeric_value ?? null, 1)}
              </div>
            </div>
            <div>
              <div className="flex items-center justify-between gap-2">
                <div className="text-[10px] font-semibold tracking-wide text-muted-foreground uppercase">
                  H-Return Adj.
                </div>
                <AgeBadge iso={data.h_return_adjusted_ts} />
              </div>
              <div className={`mt-0.5 text-2xl font-bold tabular-nums ${hReturnTone(data.h_return_adjusted_value)}`}>
                {formatPercent(data.h_return_adjusted_value, 1)}
              </div>
            </div>
          </div>

          {/* KPI-Bestandteile des H-Returns */}
          <div className="mt-2 grid grid-cols-3 gap-x-4 gap-y-1.5 border-t border-border/40 pt-2">
            {KPI_ITEMS.map((item) => (
              <div key={item.key} className="min-w-0">
                <div className="truncate text-[10px] text-muted-foreground" title={item.label}>
                  {item.label}
                </div>
                <div className="text-[11px] font-medium tabular-nums text-foreground">
                  {formatKpi(data.fy_kpis[item.key], item.kind)}
                </div>
              </div>
            ))}
          </div>
        </div>
      </div>
    </Card>
  );
}
