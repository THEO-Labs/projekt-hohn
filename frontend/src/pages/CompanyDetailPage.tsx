import { Fragment, useEffect, useMemo, useState } from "react";
import { Link, useParams } from "react-router-dom";
import { ChevronLeft, Loader2 } from "lucide-react";
import { toast } from "sonner";

import { AppHeader } from "@/components/AppHeader";
import { useAuth } from "@/hooks/useAuth";
import { listCompanies, type Company } from "@/api/companies";
import {
  getDashboardDefinitions,
  loadCompanyCard,
  type CompanyCardData,
} from "@/api/dashboard";
import {
  getCompanyValues,
  type CompanyValue,
  type ValueDefinition,
} from "@/api/values";
import {
  AgeBadge,
  OverviewRow,
  RefreshActions,
  hReturnTone,
} from "@/components/companyCardBits";
import {
  formatFyEnd,
  formatNumber,
  formatPercent,
  formatShort,
} from "@/lib/format";
import {
  QUARTERLY_SECTIONS,
  BALANCE_SHEET,
  detailToBalanceSheet,
  detailToQuarterlySections,
  type QuarterlyBlockData,
  type QuarterlySection,
  type BalanceSheetSection,
  type BSData,
} from "./companyDetailMocks";
import { CollapsibleCard } from "@/components/CollapsibleCard";
import { getCompanyDetail, type CompanyDetailOut } from "@/api/detail";
import { getFxRates, DISPLAY_CURRENCIES, convertCurrency } from "@/api/fx";
import { ValueCellPopover, cellColorClass } from "@/components/ValueCellPopover";
import type { Cell } from "./companyDetailMocks";
import { FORMULAS } from "./companyDetailMocks";

const METRIC_KEYS = [
  "hohn_return_detailed",
  "dividend_yield",
  "ni_growth",
  "net_buyback_yield",
  "net_debt_change_pct",
  "h_peg",
  "pe_ratio",
  "fcf_yield",
  "ev_ebitda",
];

type Variant = "gaap" | "adjusted";

type MetricRowSpec = {
  label: string;
  key: string | null;              // value_key falls im Backend vorhanden
  computed?: (rows: Record<string, CompanyValue | undefined>, variant: Variant) => number | null;
  format: (v: number | null) => string;
  tone?: (v: number | null) => string;
};

const pct = (v: number | null) => formatPercent(v, 2);
const pct1 = (v: number | null) => formatPercent(v, 1);
const num = (v: number | null) => formatNumber(v, 2);

function pickValue(row: CompanyValue | undefined, variant: Variant): number | null {
  if (!row) return null;
  // For adjusted: silent fallback to GAAP when no separate Non-GAAP is reported
  // (Excel convention: Non-GAAP == GAAP when no adjustments exist).
  let raw = variant === "gaap" ? row.numeric_value : row.numeric_value_adjusted;
  if (variant === "adjusted" && raw == null) raw = row.numeric_value;
  if (raw == null) return null;
  const n = Number(raw);
  return Number.isFinite(n) ? n : null;
}

function computePeg(
  rows: Record<string, CompanyValue | undefined>,
  variant: Variant,
): number | null {
  const pe = pickValue(rows.pe_ratio, variant);
  const growth = pickValue(rows.ni_growth, variant);
  if (pe == null || growth == null || growth <= 0) return null;
  return pe / growth;
}

const SECTIONS: { title: string; rows: MetricRowSpec[] }[] = [
  {
    title: "Return Drivers",
    rows: [
      { label: "H-Return", key: "hohn_return_detailed", format: pct1, tone: hReturnTone },
      { label: "Dividend Yield", key: "dividend_yield", format: pct },
      { label: "Net Income Growth", key: "ni_growth", format: pct },
      { label: "Net Buyback Yield", key: "net_buyback_yield", format: pct },
      { label: "Δ Net Debt Yield", key: "net_debt_change_pct", format: pct },
    ],
  },
  {
    title: "PEG",
    rows: [{ label: "H-PEG", key: "h_peg", format: num }],
  },
  {
    title: "Valuation (TTM)",
    rows: [
      { label: "PE Ratio", key: "pe_ratio", format: num },
      { label: "FCF Yield", key: "fcf_yield", format: pct },
      { label: "EV / EBITDA", key: "ev_ebitda", format: num },
    ],
  },
  {
    title: "Other Multiples",
    rows: [
      { label: "PEG", key: null, computed: computePeg, format: num },
      { label: "PS Ratio", key: "ps_ratio", format: num },
      { label: "Net Debt / Op. Cashflow", key: "net_debt_to_ocf", format: num },
    ],
  },
];

function MetricValue({
  value,
  format,
  tone,
  cell,
  onOpen,
}: {
  value: number | null;
  format: (v: number | null) => string;
  tone?: (v: number | null) => string;
  cell?: Cell;
  onOpen?: (cell: Cell, displayValue: string, anchor: DOMRect) => void;
}) {
  const rawLabel = format(value);
  const label = cell?.is_gaap_fallback && value !== null ? `≈ ${rawLabel}` : rawLabel;
  const toneCls = tone ? tone(value) : cell ? cellColorClass(cell) : value == null ? "text-muted-foreground/70" : "text-foreground";
  const clickable = !!cell && !!onOpen && value !== null;
  const clickCls = clickable ? "cursor-pointer rounded px-1 -mx-1 transition-colors hover:bg-muted/50" : "";
  return (
    <span
      className={`text-[13px] font-semibold tabular-nums ${toneCls} ${clickCls}`}
      title={cell?.is_gaap_fallback ? "Non-GAAP nicht separat reported — zeigt GAAP-Wert" : undefined}
      onClick={
        clickable
          ? (e) => {
              e.stopPropagation();
              onOpen!(cell!, label, (e.currentTarget as HTMLElement).getBoundingClientRect());
            }
          : undefined
      }
    >
      {label}
    </span>
  );
}

function cvToMetricCell(
  cv: CompanyValue | undefined,
  variant: "gaap" | "adjusted",
  valueKey: string,
  periodLabel: string,
): Cell {
  // For adjusted column: fall back to GAAP when no separate Non-GAAP is reported.
  let raw = variant === "gaap" ? cv?.numeric_value : cv?.numeric_value_adjusted;
  if (variant === "adjusted" && (raw == null || raw === undefined)) {
    raw = cv?.numeric_value;
  }
  const value = raw != null && raw !== undefined ? Number(raw) : null;
  return {
    value,
    source_name: cv?.source_name ?? null,
    source_link: cv?.source_link ?? null,
    fetched_at: cv?.fetched_at ?? null,
    primary_method: cv?.primary_method ?? "calculated",
    manually_overridden: cv?.manually_overridden ?? false,
    formula: FORMULAS[valueKey] ?? null,
    value_key: valueKey,
    period_label: periodLabel,
  };
}

function computedMetricCell(value: number | null, formula: string, periodLabel: string): Cell {
  return {
    value,
    primary_method: "calculated",
    formula,
    period_label: periodLabel,
  };
}

// Shared column split for ALL detail-page cards:
// Outer container is 50/50 (GAAP-Half | Non-GAAP-Half). Every card renders its
// GAAP content in the left half and its adjusted content in the right half so
// the vertical divider between them lines up across cards.
const HALVES = "grid grid-cols-1 gap-x-10 md:grid-cols-2";
// Inside the GAAP-Half of MetricsCard: label fills, value hugs the midline.
const HALF_LEFT_INNER = "grid grid-cols-[minmax(0,1fr)_auto] items-baseline gap-x-4";

function MetricsBody({
  rowsByKey,
  fyYear,
  gaapLabel,
  onOpenCell,
}: {
  rowsByKey: Record<string, CompanyValue | undefined>;
  fyYear: number | null;
  gaapLabel: string;
  onOpenCell: (cell: Cell, displayValue: string, anchor: DOMRect) => void;
}) {
  return (
    <>
      {/* Column header row */}
      <div className={`${HALVES} items-center border-b border-border/40 px-6 py-2`}>
        <div className={HALF_LEFT_INNER}>
          <div className="text-[10px] font-semibold tracking-wider uppercase text-muted-foreground">
            Metric
          </div>
          <div className="flex items-center justify-end gap-1.5 text-[10px] font-semibold tracking-wider uppercase text-sky-700">
            <span className="h-2 w-2 rounded-full bg-sky-500" />
            {gaapLabel}
          </div>
        </div>
        <div className="flex items-center justify-end gap-1.5 text-[10px] font-semibold tracking-wider uppercase text-emerald-700">
          <span className="h-2 w-2 rounded-full bg-emerald-500" />
          Adjusted
        </div>
      </div>
      <div className="px-6 py-3">
        {SECTIONS.map((section, si) => (
          <div key={section.title} className={si > 0 ? "mt-2.5 border-t border-border/40 pt-2.5" : ""}>
            <div className="mb-1 text-[10px] font-semibold tracking-wide text-muted-foreground/70 uppercase">
              {section.title}
            </div>
            {section.rows.map((r) => {
              const gaapVal = r.computed
                ? r.computed(rowsByKey, "gaap")
                : r.key
                  ? pickValue(rowsByKey[r.key], "gaap")
                  : null;
              const adjVal = r.computed
                ? r.computed(rowsByKey, "adjusted")
                : r.key
                  ? pickValue(rowsByKey[r.key], "adjusted")
                  : null;
              const periodLabel = `${r.label} FY ${fyYear ?? "?"}`;
              const gaapCell: Cell = r.computed
                ? computedMetricCell(gaapVal, r.label === "PEG" ? "PE Ratio / NI Growth" : "", periodLabel)
                : r.key
                  ? cvToMetricCell(rowsByKey[r.key], "gaap", r.key, periodLabel)
                  : { value: gaapVal, period_label: periodLabel };
              const adjCell: Cell = r.computed
                ? computedMetricCell(adjVal, r.label === "PEG" ? "PE Ratio / NI Growth" : "", periodLabel)
                : r.key
                  ? cvToMetricCell(rowsByKey[r.key], "adjusted", r.key, periodLabel)
                  : { value: adjVal, period_label: periodLabel };
              return (
                <div key={r.label} className={`${HALVES} items-center py-[3px]`}>
                  <div className={HALF_LEFT_INNER}>
                    <div className="text-[12.5px] text-muted-foreground">{r.label}</div>
                    <div className="text-right">
                      <MetricValue value={gaapVal} format={r.format} tone={r.tone} cell={gaapCell} onOpen={onOpenCell} />
                    </div>
                  </div>
                  <div className="text-right">
                    <MetricValue value={adjVal} format={r.format} tone={r.tone} cell={adjCell} onOpen={onOpenCell} />
                  </div>
                </div>
              );
            })}
          </div>
        ))}
      </div>
    </>
  );
}

function CurrencySelect({
  value,
  nativeCurrency,
  onChange,
  fxRates,
}: {
  value: string;
  nativeCurrency: string;
  onChange: (c: string) => void;
  fxRates: Record<string, number> | null;
}) {
  const options = useMemo(() => {
    const set = new Set<string>(DISPLAY_CURRENCIES);
    set.add(nativeCurrency);
    return Array.from(set);
  }, [nativeCurrency]);
  return (
    <select
      value={value}
      onChange={(e) => onChange(e.target.value)}
      className="h-7 rounded-lg border border-border bg-background px-2 text-[12px] font-mono text-foreground outline-none transition-colors hover:bg-muted focus:border-primary"
      title={fxRates ? "Convert display currency" : "Convert display currency (FX rates loading…)"}
    >
      {options.map((c) => (
        <option key={c} value={c}>
          {c}
        </option>
      ))}
    </select>
  );
}

function UnitInfo({ unit }: { unit?: string }) {
  if (!unit) return null;
  return <span className="text-[11px] text-muted-foreground">in {unit}</span>;
}

// -- Quarterly / Annual dual-block card -----------------------------------

type ActiveCell = { cell: Cell; displayValue: string; anchor: DOMRect } | null;

function pctChange(now: number | null, prev: number | null): number | null {
  if (now == null || prev == null || prev === 0) return null;
  return ((now - prev) / prev) * 100;
}

function ClickableCell({
  cell,
  fmt,
  onOpen,
  className = "",
}: {
  cell: Cell;
  fmt: (v: number | null) => string;
  onOpen: (cell: Cell, displayValue: string, anchor: DOMRect) => void;
  className?: string;
}) {
  const rawLabel = fmt(cell.value);
  const label = cell.is_gaap_fallback && cell.value !== null ? `≈ ${rawLabel}` : rawLabel;
  const hasMeta =
    (cell.value !== null &&
      (cell.source_name || cell.source_link || cell.formula || cell.primary_method || cell.is_gaap_fallback)) ||
    cell.primary_method === "not_found";
  const clickable = hasMeta;
  return (
    <td
      className={`px-1.5 py-[3px] text-right tabular-nums text-[12px] ${cellColorClass(cell)} ${
        clickable ? "cursor-pointer transition-colors hover:bg-muted/50" : ""
      } ${className}`}
      onClick={
        clickable
          ? (e) => {
              e.stopPropagation();
              onOpen(cell, label, (e.currentTarget as HTMLElement).getBoundingClientRect());
            }
          : undefined
      }
      title={
        cell.primary_method === "not_found"
          ? "Recherche ohne Fund — Wert manuell raussuchen"
          : cell.is_gaap_fallback
            ? "Non-GAAP nicht separat reported — zeigt GAAP-Wert"
            : undefined
      }
    >
      {label}
    </td>
  );
}

function QuarterlyBlock({
  data,
  variant,
  gaapLabel,
  showAnnual = true,
  onOpenCell,
  unit,
}: {
  data: QuarterlyBlockData;
  variant: "gaap" | "adjusted";
  gaapLabel: string;
  showAnnual?: boolean;
  unit?: string;
  onOpenCell: (cell: Cell, displayValue: string, anchor: DOMRect) => void;
}) {
  const [current, prior] = data.rows;
  const changes: Cell[] | null =
    current && prior
      ? (["q1", "q2", "q3", "q4", "annual"] as const).map((q) => {
          const c = pctChange(current[q].value, prior[q].value);
          return {
            value: c,
            formula: "(current − prior) / prior × 100",
            primary_method: "calculated",
            period_label: `Δ ${q.toUpperCase()} ${current.year} vs ${prior.year}`,
          };
        })
      : null;

  const head = "px-1.5 py-1 text-[10px] font-semibold tracking-wide uppercase text-muted-foreground text-right";

  const dotCls = variant === "gaap" ? "bg-sky-500" : "bg-emerald-500";
  const label = variant === "gaap" ? gaapLabel : "Adjusted";
  const labelCls = variant === "gaap" ? "text-sky-700" : "text-emerald-700";

  const isPerShare = !!unit && !unit.includes("millions");
  const defaultFmt = (v: number | null) => formatNumber(v, isPerShare ? 2 : 0);
  const pctFmt = (v: number | null) => formatPercent(v, 1);

  return (
    <div className="min-w-0">
      <div className="mb-1.5 flex items-center gap-1.5">
        <span className={`h-2 w-2 rounded-full ${dotCls}`} />
        <div className={`text-[10px] font-semibold tracking-wider uppercase ${labelCls}`}>{label}</div>
      </div>
      <table className="w-full">
        <thead>
          <tr>
            <th className={`${head} text-left`}></th>
            <th className={head}>Q1</th>
            <th className={head}>Q2</th>
            <th className={head}>Q3</th>
            <th className={head}>Q4</th>
            {showAnnual && <th className={`${head} border-l border-border/40`}>Annual</th>}
          </tr>
        </thead>
        <tbody>
          {data.rows.map((r) => (
            <tr key={r.year} className="border-t border-border/40">
              <td className="px-1.5 py-[3px] text-left text-[12px] font-medium text-muted-foreground">{r.year}</td>
              <ClickableCell cell={r.q1} fmt={defaultFmt} onOpen={onOpenCell} />
              <ClickableCell cell={r.q2} fmt={defaultFmt} onOpen={onOpenCell} />
              <ClickableCell cell={r.q3} fmt={defaultFmt} onOpen={onOpenCell} />
              <ClickableCell cell={r.q4} fmt={defaultFmt} onOpen={onOpenCell} />
              {showAnnual && (
                <ClickableCell cell={r.annual} fmt={defaultFmt} onOpen={onOpenCell} className="border-l border-border/40 font-semibold" />
              )}
            </tr>
          ))}
          {changes && (
            <tr className="border-t border-border/40">
              <td className="px-1.5 py-[3px] text-left text-[12px] font-medium text-muted-foreground">Δ %</td>
              <ClickableCell cell={changes[0]} fmt={pctFmt} onOpen={onOpenCell} />
              <ClickableCell cell={changes[1]} fmt={pctFmt} onOpen={onOpenCell} />
              <ClickableCell cell={changes[2]} fmt={pctFmt} onOpen={onOpenCell} />
              <ClickableCell cell={changes[3]} fmt={pctFmt} onOpen={onOpenCell} />
              {showAnnual && (
                <ClickableCell cell={changes[4]} fmt={pctFmt} onOpen={onOpenCell} className="border-l border-border/40 font-semibold" />
              )}
            </tr>
          )}
          {data.extras?.map((x) => {
            const fmt = x.format ?? defaultFmt;
            return (
              <tr key={x.label} className="border-t border-border/40">
                <td className="px-1.5 py-[3px] text-left text-[12px] font-medium text-muted-foreground">{x.label}</td>
                <ClickableCell cell={x.q1} fmt={fmt} onOpen={onOpenCell} />
                <ClickableCell cell={x.q2} fmt={fmt} onOpen={onOpenCell} />
                <ClickableCell cell={x.q3} fmt={fmt} onOpen={onOpenCell} />
                <ClickableCell cell={x.q4} fmt={fmt} onOpen={onOpenCell} />
                {showAnnual && (
                  <ClickableCell cell={x.annual} fmt={fmt} onOpen={onOpenCell} className="border-l border-border/40 font-semibold" />
                )}
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}

function QuarterlyDualBody({
  gaap,
  adjusted,
  gaapLabel,
  showAnnual = true,
  onOpenCell,
  unit,
}: {
  unit?: string;
  gaap: QuarterlyBlockData;
  adjusted: QuarterlyBlockData;
  gaapLabel: string;
  showAnnual?: boolean;
  onOpenCell: (cell: Cell, displayValue: string, anchor: DOMRect) => void;
}) {
  return (
    <div className={`${HALVES} px-6 py-3`}>
      <QuarterlyBlock data={gaap} variant="gaap" gaapLabel={gaapLabel} showAnnual={showAnnual} onOpenCell={onOpenCell} unit={unit} />
      <QuarterlyBlock data={adjusted} variant="adjusted" gaapLabel={gaapLabel} showAnnual={showAnnual} onOpenCell={onOpenCell} unit={unit} />
    </div>
  );
}

// -- Balance sheet block ---------------------------------------------------

function BalanceSheetBlock({
  data,
  variant,
  gaapLabel,
  currentYear,
  priorYear,
  onOpenCell,
}: {
  data: BSData;
  variant: "gaap" | "adjusted";
  gaapLabel: string;
  currentYear: number;
  priorYear: number;
  onOpenCell: (cell: Cell, displayValue: string, anchor: DOMRect) => void;
}) {
  const head = "px-1.5 py-1 text-[10px] font-semibold tracking-wide uppercase text-muted-foreground text-right";

  const dotCls = variant === "gaap" ? "bg-sky-500" : "bg-emerald-500";
  const label = variant === "gaap" ? gaapLabel : "Adjusted";
  const labelCls = variant === "gaap" ? "text-sky-700" : "text-emerald-700";

  return (
    <div className="min-w-0">
      <div className="mb-1.5 flex items-center gap-1.5">
        <span className={`h-2 w-2 rounded-full ${dotCls}`} />
        <div className={`text-[10px] font-semibold tracking-wider uppercase ${labelCls}`}>
          {label}
        </div>
      </div>
      <table className="w-full">
        <thead>
          <tr>
            <th className={`${head} text-left`}></th>
            <th className={head}>{currentYear}</th>
            <th className={head}>{priorYear}</th>
          </tr>
        </thead>
        <tbody>
          {data.groups.map((group, gi) => (
            <Fragment key={gi}>
              {gi > 0 && (
                <tr>
                  <td colSpan={3} className="h-2" />
                </tr>
              )}
              {group.rows.map((r) => {
                const fmt = r.format ?? ((v: number | null) => formatNumber(v, 0));
                const emph = r.emphasis ? "font-semibold" : "";
                const border = r.emphasis ? "border-t border-border/60" : "border-t border-border/30";
                return (
                  <tr key={r.label} className={border}>
                    <td className={`px-1.5 py-[3px] text-left text-[12px] font-medium text-muted-foreground ${emph}`}>{r.label}</td>
                    <ClickableCell cell={r.y1} fmt={fmt} onOpen={onOpenCell} className={emph} />
                    <ClickableCell cell={r.y2} fmt={fmt} onOpen={onOpenCell} className={emph} />
                  </tr>
                );
              })}
            </Fragment>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function BalanceSheetBody({
  sec,
  gaapLabel,
  onOpenCell,
}: {
  sec: BalanceSheetSection;
  gaapLabel: string;
  onOpenCell: (cell: Cell, displayValue: string, anchor: DOMRect) => void;
}) {
  return (
    <div className={`${HALVES} px-6 py-3`}>
      <BalanceSheetBlock data={sec.gaap} variant="gaap" gaapLabel={gaapLabel} currentYear={sec.currentYear} priorYear={sec.priorYear} onOpenCell={onOpenCell} />
      <BalanceSheetBlock data={sec.adjusted} variant="adjusted" gaapLabel={gaapLabel} currentYear={sec.currentYear} priorYear={sec.priorYear} onOpenCell={onOpenCell} />
    </div>
  );
}

// -- Section navigation ----------------------------------------------------

function slugify(s: string) {
  return s
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, "-")
    .replace(/^-|-$/g, "");
}

function useActiveSection(ids: string[]): string | null {
  const [active, setActive] = useState<string | null>(ids[0] ?? null);
  useEffect(() => {
    if (ids.length === 0) return;
    const els = ids.map((id) => document.getElementById(id)).filter((x): x is HTMLElement => !!x);
    if (els.length === 0) return;
    const visible = new Map<string, number>();
    const observer = new IntersectionObserver(
      (entries) => {
        for (const e of entries) {
          const id = e.target.id;
          if (e.isIntersecting) visible.set(id, e.intersectionRatio);
          else visible.delete(id);
        }
        if (visible.size > 0) {
          const [topId] = [...visible.entries()].sort((a, b) => b[1] - a[1])[0];
          setActive(topId);
        }
      },
      { rootMargin: "-100px 0px -60% 0px", threshold: [0, 0.25, 0.5, 0.75, 1] },
    );
    els.forEach((el) => observer.observe(el));
    return () => observer.disconnect();
  }, [ids.join("|")]);  // eslint-disable-line react-hooks/exhaustive-deps
  return active;
}

function DetailNav({
  items,
  activeId,
}: {
  items: { id: string; title: string }[];
  activeId: string | null;
}) {
  return (
    <nav className="flex flex-col gap-0.5">
      <div className="mb-2 px-2 text-[10px] font-semibold tracking-wider uppercase text-muted-foreground/70">
        Sections
      </div>
      {items.map((it) => {
        const active = it.id === activeId;
        return (
          <a
            key={it.id}
            href={`#${it.id}`}
            onClick={(e) => {
              e.preventDefault();
              const el = document.getElementById(it.id);
              if (el) {
                el.scrollIntoView({ behavior: "smooth", block: "start" });
                history.replaceState(null, "", `#${it.id}`);
              }
            }}
            className={`group flex items-center gap-2 rounded-md px-2 py-1.5 text-[12.5px] transition-colors ${
              active
                ? "bg-primary/10 font-medium text-primary"
                : "text-muted-foreground hover:bg-muted hover:text-foreground"
            }`}
          >
            <span
              className={`h-1.5 w-1.5 rounded-full transition-colors ${
                active ? "bg-primary" : "bg-transparent group-hover:bg-muted-foreground/50"
              }`}
            />
            <span className="truncate">{it.title}</span>
          </a>
        );
      })}
    </nav>
  );
}

export function CompanyDetailPage() {
  const { pid, cid } = useParams<{ pid: string; cid: string }>();
  const { user, logout } = useAuth();

  const [loading, setLoading] = useState(true);
  const [company, setCompany] = useState<Company | null>(null);
  const [card, setCard] = useState<CompanyCardData | null>(null);
  const [fyRows, setFyRows] = useState<CompanyValue[]>([]);
  const [definitions, setDefinitions] = useState<ValueDefinition[]>([]);
  const [detail, setDetail] = useState<CompanyDetailOut | null>(null);
  const [fxRates, setFxRates] = useState<Record<string, number> | null>(null);
  const [displayCurrency, setDisplayCurrency] = useState<string | null>(null);

  const loadAll = async () => {
    if (!pid || !cid) return;
    setLoading(true);
    try {
      const companies = await listCompanies(pid);
      const c = companies.find((x) => x.id === cid) ?? null;
      if (!c) {
        toast.error("Company not found");
        setLoading(false);
        return;
      }
      setCompany(c);
      const [cardData, defs, fy, detailData, fx] = await Promise.all([
        loadCompanyCard(c),
        getDashboardDefinitions().catch(() => [] as ValueDefinition[]),
        getCompanyValues(c.id, "FY").catch(() => [] as CompanyValue[]),
        getCompanyDetail(c.id).catch(() => null),
        getFxRates().catch(() => null),
      ]);
      setCard(cardData);
      setDefinitions(defs);
      setFyRows(fy);
      setDetail(detailData);
      if (fx) setFxRates(fx.rates);
      // Default display currency = company's native currency.
      setDisplayCurrency((prev) => prev ?? c.currency);
    } catch (err) {
      console.error("CompanyDetail-Load fehlgeschlagen", err);
      toast.error("Failed to load company");
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    loadAll();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [pid, cid]);

  const fyYear = card?.fy_estimate_year ?? null;

  const rowsByKey = useMemo<Record<string, CompanyValue | undefined>>(() => {
    if (!fyYear) return {};
    const map: Record<string, CompanyValue | undefined> = {};
    for (const k of METRIC_KEYS) {
      const match = fyRows.find((r) => r.value_key === k && r.period_year === fyYear);
      map[k] = match;
    }
    return map;
  }, [fyRows, fyYear]);

  if (!user) return null;

  return (
    <div className="min-h-screen bg-background">
      <AppHeader email={user.email} onLogout={logout} />
      <main className="mx-auto max-w-6xl px-6 py-8">
        <div className="mb-4">
          <Link
            to={`/portfolios/${pid}`}
            className="inline-flex items-center gap-1 text-sm text-muted-foreground transition-colors hover:text-foreground"
          >
            <ChevronLeft className="h-3.5 w-3.5" />
            Portfolio
          </Link>
        </div>

        {loading ? (
          <div className="flex items-center justify-center py-24 text-muted-foreground">
            <Loader2 className="mr-2 h-4 w-4 animate-spin" /> Loading company…
          </div>
        ) : !company || !card ? (
          <div className="rounded-xl border border-border/60 bg-card p-8 text-center text-sm text-muted-foreground">
            Company not found.
          </div>
        ) : (
          <CompanyDetailContent
            company={company}
            card={card}
            fyYear={fyYear}
            definitions={definitions}
            rowsByKey={rowsByKey}
            reload={loadAll}
            setCard={setCard}
            detail={detail}
            fxRates={fxRates}
            displayCurrency={displayCurrency ?? company.currency}
            onCurrencyChange={setDisplayCurrency}
          />
        )}
      </main>
    </div>
  );
}

// --------------------------------------------------------------------------

function CompanyDetailContent({
  company,
  card,
  fyYear,
  definitions,
  rowsByKey,
  reload,
  setCard,
  detail,
  fxRates,
  displayCurrency,
  onCurrencyChange,
}: {
  company: Company;
  card: CompanyCardData;
  fyYear: number | null;
  definitions: ValueDefinition[];
  rowsByKey: Record<string, CompanyValue | undefined>;
  reload: () => void;
  setCard: (d: CompanyCardData) => void;
  detail: CompanyDetailOut | null;
  fxRates: Record<string, number> | null;
  displayCurrency: string;
  onCurrencyChange: (c: string) => void;
}) {
  const OVERVIEW_ID = "overview-values";
  const METRICS_ID = "overview-metrics";
  const BS_ID = "balance-sheet";

  const fxCtx = useMemo(() => ({ displayCurrency, rates: fxRates }), [displayCurrency, fxRates]);
  const quarterlySections: QuarterlySection[] = useMemo(
    () => (detail ? detailToQuarterlySections(detail, fxCtx) : QUARTERLY_SECTIONS),
    [detail, fxCtx],
  );
  const balanceSheet: BalanceSheetSection = useMemo(
    () => (detail ? detailToBalanceSheet(detail, fxCtx) : BALANCE_SHEET),
    [detail, fxCtx],
  );

  const [activeCell, setActiveCell] = useState<ActiveCell>(null);
  const openCell = (cell: Cell, displayValue: string, anchor: DOMRect) => {
    setActiveCell({ cell, displayValue, anchor });
  };
  const closeCell = () => setActiveCell(null);

  const navItems = useMemo(() => {
    return [
      { id: OVERVIEW_ID, title: "Overview" },
      { id: METRICS_ID, title: "Overview metrics" },
      ...quarterlySections.map((s) => ({ id: `sec-${slugify(s.title)}`, title: s.title })),
      { id: BS_ID, title: "Balance Sheet" },
    ];
  }, [quarterlySections]);

  const ids = useMemo(() => navItems.map((n) => n.id), [navItems]);
  const activeId = useActiveSection(ids);

  return (
    <div className="flex flex-col gap-8 md:flex-row">
      <aside className="hidden md:block md:w-52 md:shrink-0">
        <div className="sticky top-6">
          <DetailNav items={navItems} activeId={activeId} />
        </div>
      </aside>

      <div className="min-w-0 flex-1 space-y-5">
        {/* Overview values — with company header + refresh actions */}
        <CollapsibleCard
          id={OVERVIEW_ID}
          title={
            <span className="flex items-center gap-2.5">
              <span className="text-[14px] font-semibold text-foreground">{company.name}</span>
              <span className="rounded bg-primary/10 px-1.5 py-0.5 font-mono text-[10px] font-semibold text-primary">
                {company.ticker}
              </span>
              <span className="rounded bg-muted px-1.5 py-0.5 font-mono text-[10px] font-medium text-muted-foreground">
                {company.currency}
              </span>
              {fyYear && (
                <span className="rounded bg-violet-100 px-1.5 py-0.5 font-mono text-[10px] font-medium text-violet-700 ring-1 ring-violet-200">
                  FY {fyYear}
                </span>
              )}
            </span>
          }
          right={
            <div onClick={(e) => e.stopPropagation()} className="flex items-center gap-2">
              <CurrencySelect
                value={displayCurrency}
                nativeCurrency={company.currency}
                onChange={onCurrencyChange}
                fxRates={fxRates}
              />
              <RefreshActions
                company={company}
                fyEstimateYear={fyYear}
                definitions={definitions}
                onRefreshed={(d) => { setCard(d); reload(); }}
                variant="verbose"
              />
            </div>
          }
        >
          <div className="grid grid-cols-1 gap-x-16 gap-y-1 px-6 py-3 md:grid-cols-2">
            <div>
              <OverviewRow label="Stock price" value={formatShort(convertCurrency(card.stock_price?.numeric_value ?? null, company.currency, displayCurrency, fxRates ?? {}), displayCurrency)} ts={card.stock_price?.fetched_at} />
              <OverviewRow label="Shares outstanding" value={formatShort(card.shares_outstanding?.numeric_value ?? null)} ts={card.shares_outstanding?.fetched_at} />
              <OverviewRow label="Market Cap" value={formatShort(convertCurrency(card.market_cap?.numeric_value ?? null, company.currency, displayCurrency, fxRates ?? {}), displayCurrency)} ts={card.market_cap?.fetched_at} />
              <OverviewRow label="Enterprise Value" value={formatShort(convertCurrency(card.enterprise_value, company.currency, displayCurrency, fxRates ?? {}), displayCurrency)} ts={card.enterprise_value_ts} />
            </div>
            <div>
              <OverviewRow label="Date" value={new Date().toLocaleDateString("en-GB", { day: "2-digit", month: "long", year: "numeric" })} />
              <OverviewRow label="Fiscal Year End" value={formatFyEnd(company.fiscal_year_end_month, company.fiscal_year_end_day)} />
              <OverviewRow label="Next Earnings Release" value={<span className="text-muted-foreground/60">—</span>} />
            </div>
          </div>
        </CollapsibleCard>

        <div className="flex flex-wrap items-center gap-x-4 gap-y-1 rounded-lg border border-border/50 bg-muted/30 px-3 py-2 text-[11px] text-muted-foreground">
          <span className="font-semibold uppercase tracking-wide text-[10px]">Legende</span>
          <span className="flex items-center gap-1"><span className="h-2 w-2 rounded-full bg-foreground" /> Actual (berichtet)</span>
          <span className="flex items-center gap-1"><span className="h-2 w-2 rounded-full bg-sky-500" /> Estimate</span>
          <span className="flex items-center gap-1"><span className="h-2 w-2 rounded-full bg-violet-500" /> Calculated (Formel)</span>
          <span className="flex items-center gap-1"><span className="h-2 w-2 rounded-full bg-amber-500" /> Manuell</span>
          <span className="flex items-center gap-1"><span className="h-2 w-2 rounded-full bg-emerald-500" /> Verified (2-Stage)</span>
          <span className="flex items-center gap-1"><span className="h-2 w-2 rounded-full bg-red-500" /> Nicht gefunden — manuell recherchieren</span>
        </div>

        <CollapsibleCard id={METRICS_ID} title="Overview metrics">
          <MetricsBody rowsByKey={rowsByKey} fyYear={fyYear} gaapLabel={company.accounting_standard} onOpenCell={openCell} />
        </CollapsibleCard>

        {quarterlySections.map((s) => (
          <CollapsibleCard
            key={s.title}
            id={`sec-${slugify(s.title)}`}
            title={s.title}
            right={<UnitInfo unit={s.unit} />}
          >
            <QuarterlyDualBody
              gaap={s.gaap}
              adjusted={s.adjusted}
              gaapLabel={company.accounting_standard}
              showAnnual={s.showAnnual}
              onOpenCell={openCell}
              unit={s.unit}
            />
          </CollapsibleCard>
        ))}

        <CollapsibleCard
          id={BS_ID}
          title="Balance Sheet"
          right={<UnitInfo unit={balanceSheet.unit} />}
        >
          <BalanceSheetBody sec={balanceSheet} gaapLabel={company.accounting_standard} onOpenCell={openCell} />
        </CollapsibleCard>

        {activeCell && (
          <ValueCellPopover
            cell={activeCell.cell}
            displayValue={activeCell.displayValue}
            anchorRect={activeCell.anchor}
            onClose={closeCell}
            companyId={company?.id}
            onSaved={() => { reload(); }}
          />
        )}

        <div className="flex items-center justify-end gap-2 pt-2 text-[11px] text-muted-foreground">
          <span>Estimates last updated:</span>
          <AgeBadge iso={card.h_return_gaap?.fetched_at} />
        </div>
      </div>
    </div>
  );
}
