import { type RefreshStatus } from "@/api/values";

type Props = {
  companyName: string;
  status: RefreshStatus;
};

export function RefreshProgressBar({ companyName, status }: Props) {
  if (status.status !== "running") return null;

  const pct = status.total > 0 ? Math.round((status.completed / status.total) * 100) : 0;

  return (
    <div className="rounded-lg border border-border/60 bg-card px-4 py-3">
      <div className="mb-2 flex items-center justify-between text-sm">
        <span className="font-medium text-foreground">
          Berechne Werte fur {companyName}
          {status.current_key && (
            <span className="ml-1 text-muted-foreground">({status.current_key})</span>
          )}
        </span>
        <span className="text-muted-foreground">
          {status.completed} / {status.total} Werte ({pct}%)
        </span>
      </div>
      <div className="h-2 w-full overflow-hidden rounded-full bg-muted">
        <div
          className="h-full rounded-full bg-primary transition-all duration-300"
          style={{ width: `${pct}%` }}
        />
      </div>
    </div>
  );
}
