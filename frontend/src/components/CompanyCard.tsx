import { useEffect, useRef, useState } from "react";
import { Link } from "react-router-dom";
import { MoreHorizontal, Trash2, Pencil } from "lucide-react";
import { toast } from "sonner";

import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import type { CompanyCardData } from "@/api/dashboard";
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
  formatFyEnd,
  formatPercent,
  formatShort,
} from "@/lib/format";

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
          <OverviewRow label="Next Earnings" value={<span className="text-muted-foreground/60">—</span>} />
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
        <div className="grid grid-cols-2 gap-6 border-t border-border/40 pt-2.5 md:border-l md:border-t-0 md:pl-8 md:pt-0">
          <div>
            <div className="flex items-center justify-between gap-2">
              <div className="text-[10px] font-semibold tracking-wide text-muted-foreground uppercase">
                H-Return IFRS
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
      </div>
    </Card>
  );
}
