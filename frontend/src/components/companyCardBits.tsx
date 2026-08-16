import { useEffect, useRef, useState } from "react";
import { Loader2, RefreshCw, Sparkles } from "lucide-react";
import { toast } from "sonner";

import { Button } from "@/components/ui/button";
import type { CompanyCardData } from "@/api/dashboard";
import { loadCompanyCard, refreshCompanyDaily, refreshCompanyFull } from "@/api/dashboard";
import type { ValueDefinition } from "@/api/values";
import { getRefreshStatus } from "@/api/values";
import { formatAbsolute, formatRelative } from "@/lib/format";

function ageBucket(iso: string | null | undefined) {
  if (!iso) return null;
  const ms = Date.now() - new Date(iso).getTime();
  if (Number.isNaN(ms)) return null;
  const label = formatRelative(iso);
  const title = formatAbsolute(iso);
  const hr = ms / 3_600_000;
  if (hr < 6) return { label, title, cls: "bg-emerald-100 text-emerald-800 ring-emerald-200" };
  if (hr < 24) return { label, title, cls: "bg-sky-100 text-sky-800 ring-sky-200" };
  if (hr < 24 * 7) return { label, title, cls: "bg-amber-100 text-amber-800 ring-amber-200" };
  if (hr < 24 * 30) return { label, title, cls: "bg-orange-100 text-orange-800 ring-orange-200" };
  return { label, title, cls: "bg-rose-100 text-rose-800 ring-rose-200" };
}

export function AgeBadge({ iso }: { iso: string | null | undefined }) {
  const b = ageBucket(iso);
  if (!b) {
    return (
      <span className="rounded-full bg-muted px-1.5 py-0.5 text-[10px] font-medium text-muted-foreground/70 ring-1 ring-border/60">
        —
      </span>
    );
  }
  return (
    <span
      className={`rounded-full px-1.5 py-0.5 text-[10px] font-medium ring-1 ${b.cls}`}
      title={b.title}
    >
      {b.label}
    </span>
  );
}

export function OverviewRow({
  label,
  value,
  ts,
}: {
  label: string;
  value: React.ReactNode;
  ts?: string | null | undefined;
}) {
  return (
    <div className="grid grid-cols-[minmax(0,1fr)_auto_auto] items-center gap-x-3 py-0.5">
      <div className="truncate text-[12px] text-muted-foreground">{label}</div>
      <div className="text-[12.5px] font-medium tabular-nums text-foreground">{value}</div>
      <div className="min-w-[70px] text-right">
        {ts !== undefined ? <AgeBadge iso={ts} /> : <span />}
      </div>
    </div>
  );
}

export function hReturnTone(value: number | null | undefined): string {
  if (value == null || Number.isNaN(value)) return "text-muted-foreground/70";
  if (value >= 10) return "text-emerald-700";
  if (value >= 5) return "text-amber-700";
  if (value >= 0) return "text-foreground";
  return "text-rose-700";
}

// -- Refresh Actions -----------------------------------------------------

type RefreshProps = {
  company: CompanyCardData["company"];
  fyEstimateYear: number | null;
  definitions: ValueDefinition[];
  onRefreshed: (data: CompanyCardData) => void;
  variant?: "compact" | "verbose";
};

export function RefreshActions({
  company,
  fyEstimateYear,
  definitions,
  onRefreshed,
  variant = "verbose",
}: RefreshProps) {
  const [daily, setDaily] = useState(false);
  const [full, setFull] = useState(false);
  const [progress, setProgress] = useState<{
    completed: number;
    total: number;
    phaseLabel: string | null;
  } | null>(null);
  const busy = daily || full;
  const pollTimer = useRef<number | null>(null);
  const lastSeenFinishedAt = useRef<string | null>(null);
  // Ref statt State: das Interval haelt die poll-Closure des ersten Renders,
  // dort waere `full` fuer immer false und der Abschluss-Reload feuerte nie.
  const wasRunningRef = useRef(false);

  const reload = async () => {
    try {
      const fresh = await loadCompanyCard(company);
      onRefreshed(fresh);
    } catch (err) {
      console.error("Reload failed", err);
    }
  };

  // Backend-Progress-Polling: liest /refresh-status alle 2 Sekunden. Wenn
  // beim Component-Mount schon ein Job laeuft (weil User Seite refresht hat),
  // greift der Spinner sofort wieder. Wenn Job fertig -> Reload + Toast.
  const poll = async () => {
    try {
      const s = await getRefreshStatus(company.id);
      if (s.status === "running") {
        wasRunningRef.current = true;
        setFull(true);
        const label =
          s.phase_label ||
          (s.phase === "prev_year_inputs"
            ? "Prev-year backfill"
            : s.phase === "calculating"
              ? "Calculations"
              : s.current_key || "Fetching");
        setProgress({
          completed: s.completed || 0,
          total: s.total || 0,
          phaseLabel: label,
        });
      } else if (s.status === "done" || s.status === "failed" || s.status === "idle") {
        // Nur reload+toast wenn wir vorher einen "running" gesehen haben und
        // dieser Job seit letztem Poll neu-fertig ist (finished_at aendert sich).
        const wasRunning = wasRunningRef.current;
        wasRunningRef.current = false;
        setFull(false);
        setProgress(null);
        if (wasRunning && s.finished_at && s.finished_at !== lastSeenFinishedAt.current) {
          lastSeenFinishedAt.current = s.finished_at;
          await reload();
          if (s.status === "failed") {
            toast.error(`${company.ticker}: Full recompute failed`);
          } else {
            toast.success(`${company.ticker}: Full recompute finished`);
          }
        }
      }
    } catch {
      // Silent — Backend evtl. gerade nicht erreichbar
    }
  };

  useEffect(() => {
    // Initial Poll beim Mount — greift auf laufende Jobs zurueck (z.B. wenn
    // User Seite waehrend Recompute refresht).
    poll();
    pollTimer.current = window.setInterval(poll, 2000);
    return () => {
      if (pollTimer.current !== null) {
        window.clearInterval(pollTimer.current);
      }
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [company.id]);

  const doDaily = async () => {
    if (busy) return;
    setDaily(true);
    try {
      await refreshCompanyDaily(company.id);
      await reload();
      toast.success(`${company.ticker}: Daily numbers refreshed`);
    } catch {
      toast.error(`${company.ticker}: Daily refresh failed`);
    } finally {
      setDaily(false);
    }
  };

  const doFull = async () => {
    if (busy) return;
    // Geschaeftsjahr-bewusstes Zieljahr: bei frueh endenden Fiskaljahren
    // (z.B. Dynatrace Maerz, Intuit Juli) ist das Kalenderjahr bereits
    // abgeschlossen — dann ist das laufende FY year+1. Ohne fyEstimateYear
    // (frische Firma) sonst falsches, bereits abgeschlossenes Jahr.
    const runningFyYear = (): number => {
      const now = new Date();
      const y = now.getFullYear();
      const m = company.fiscal_year_end_month;
      const d = company.fiscal_year_end_day;
      if (!m || !d) return y;
      const fye = new Date(y, m - 1, d);
      return now <= fye ? y : y + 1;
    };
    const targetYear = fyEstimateYear ?? runningFyYear();
    setFull(true);
    try {
      // Kein await reload/toast hier — der Poller uebernimmt das sobald Backend
      // status=done meldet. Verhindert Duplikate wenn Response kommt und Poller
      // gleichzeitig triggert.
      await refreshCompanyFull(company.id, targetYear, definitions);
    } catch {
      setFull(false);
      toast.error(`${company.ticker}: Full recompute failed`);
    }
  };

  const compact = variant === "compact";
  const heightCls = compact ? "h-7" : "h-8";

  const progressText = progress && progress.total > 0
    ? `${progress.completed}/${progress.total}${progress.phaseLabel ? ` · ${progress.phaseLabel}` : ""}`
    : null;

  return (
    <div className="flex items-center gap-1.5">
      <Button variant="outline" size="sm" onClick={doDaily} disabled={busy} className={heightCls}>
        {daily ? <Loader2 className="h-3 w-3 animate-spin" /> : <RefreshCw className="h-3 w-3" />}
        {compact ? "Daily" : "Refresh daily"}
      </Button>
      <Button variant="default" size="sm" onClick={doFull} disabled={busy} className={heightCls}>
        {full ? <Loader2 className="h-3 w-3 animate-spin" /> : <Sparkles className="h-3 w-3" />}
        {compact ? "Full" : "Full recompute"}
      </Button>
      {progressText && (
        <span className="text-[11px] text-muted-foreground tabular-nums" title={progressText}>
          {progressText}
        </span>
      )}
    </div>
  );
}
