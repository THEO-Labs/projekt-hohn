import { type RefreshStatus } from "@/api/values";

type Props = {
  companyName: string;
  status: RefreshStatus;
};

export function RefreshProgressBar({ companyName, status }: Props) {
  if (status.status !== "running") return null;

  const fetchPct = status.total > 0 ? Math.round((status.completed / status.total) * 100) : 0;
  const successful = status.successful ?? 0;
  const phase = status.phase ?? "fetching";
  const isPostProcessing = phase === "prev_year_inputs" || phase === "calculating";
  const displayPct = isPostProcessing ? 100 : fetchPct;
  const phaseLabel = status.phase_label
    ?? (phase === "prev_year_inputs" ? "Vorjahres-Daten holen" : phase === "calculating" ? "Berechnungen aktualisieren" : null);

  return (
    <div className="rounded-lg border border-border/60 bg-card px-4 py-3">
      <div className="mb-2 flex items-center justify-between text-sm">
        <span className="font-medium text-foreground">
          Berechne Werte für {companyName}
          {phase === "fetching" && status.current_key && (
            <span className="ml-1 text-muted-foreground">({status.current_key})</span>
          )}
          {isPostProcessing && phaseLabel && (
            <span className="ml-1 text-muted-foreground">— {phaseLabel}…</span>
          )}
        </span>
        <span className="text-muted-foreground">
          {status.completed} / {status.total} verarbeitet · <span className="text-green-600">{successful} befüllt</span>
          {!isPostProcessing && <> ({fetchPct}%)</>}
          {isPostProcessing && <span className="ml-1 italic">(Aggregation läuft)</span>}
        </span>
      </div>
      <div className="h-2 w-full overflow-hidden rounded-full bg-muted">
        <div
          className={`h-full rounded-full transition-all duration-300 ${isPostProcessing ? "bg-primary/70 animate-pulse" : "bg-primary"}`}
          style={{ width: `${displayPct}%` }}
        />
      </div>
    </div>
  );
}
