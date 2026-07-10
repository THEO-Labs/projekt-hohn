import { useEffect, useMemo, useRef, useState } from "react";
import { ExternalLink, Pencil, X } from "lucide-react";
import { toast } from "sonner";

import type { Cell } from "@/pages/companyDetailMocks";
import { formatAbsolute, formatRelative } from "@/lib/format";
import { overrideValue } from "@/api/values";

type Props = {
  cell: Cell;
  displayValue: string;
  onClose: () => void;
  anchorRect: DOMRect | null;
  companyId?: string;
  onSaved?: () => void;
};

// The badge follows is_forecast (Actual vs Estimate). primary_method is a
// secondary tag that tells the user WHERE the value came from (10-Q via
// provider, 10-Q via Claude web search, manual override, calculated formula).
function cellBadge(cell: {
  is_forecast?: boolean;
  primary_method?: string | null | undefined;
  manually_overridden?: boolean;
}): { text: string; dot: string; text_cls: string } {
  const pm = cell.primary_method;
  if (pm === "two_stage_verified") {
    return { text: "Verified (corrected)", dot: "bg-emerald-600", text_cls: "text-emerald-800" };
  }
  if (pm === "two_stage_confirmed") {
    return { text: "Verified", dot: "bg-emerald-500", text_cls: "text-emerald-700" };
  }
  if (pm === "two_stage_insufficient") {
    return { text: "Verified (weak)", dot: "bg-yellow-500", text_cls: "text-yellow-800" };
  }
  if (cell.manually_overridden || pm === "manual") {
    return { text: "Manual", dot: "bg-amber-500", text_cls: "text-amber-800" };
  }
  if (pm === "calculated") {
    return { text: "Calculated", dot: "bg-violet-500", text_cls: "text-violet-700" };
  }
  if (cell.is_forecast) {
    return { text: "Estimate", dot: "bg-sky-500", text_cls: "text-sky-700" };
  }
  return { text: "Actual", dot: "bg-foreground", text_cls: "text-foreground" };
}

function methodOrigin(method: string | null | undefined): string {
  switch (method) {
    case "provider": return "via Provider";
    case "pdf": return "via PDF";
    case "web_guidance": return "via Claude Web-Search";
    case "manual": return "manual";
    case "calculated": return "computed";
    case "two_stage_confirmed": return "extractor + verifier confirmed";
    case "two_stage_verified": return "extractor + verifier corrected";
    case "two_stage_insufficient": return "extractor + verifier · weak evidence";
    default: return "";
  }
}

// Try to trim a long backend source_name into (headline, details[]).
// Backend produces strings like:
//   "SEC EDGAR 10-Q (Q3 FY2026, standalone)"
//   "Per-Q-Aggregation (Summe Q1-Q4) FY2026: 43,470 USD = Q1: SEC ... | Q2: SEC ... | Q3: Claude ... | Q4: Claude ..."
// We want a short first-line label and (optionally) the per-quarter breakdown as small lines.
function parseSource(raw: string | null | undefined): { head: string; parts: string[] } {
  if (!raw) return { head: "", parts: [] };
  const eq = raw.indexOf("=");
  if (eq === -1) return { head: raw.trim(), parts: [] };
  const headRaw = raw.slice(0, eq).trim();
  const rest = raw.slice(eq + 1).trim();
  const parts = rest
    .split("|")
    .map((s) => s.trim())
    .filter((s) => s.length > 0);
  // If head still contains the raw number after ":", drop it: "Per-Q-Aggregation (…) FY2026: 43,470 USD"
  const colon = headRaw.lastIndexOf(":");
  const head = colon > 0 ? headRaw.slice(0, colon).trim() : headRaw;
  return { head, parts };
}

// Parse Two-Stage source_name format:
//   "Two-Stage <verdict>: <reason> | flags=flag1,flag2"
// Returns null if the source doesn't look like a two-stage record.
function parseTwoStageSource(raw: string | null | undefined): {
  verdict: string;
  reason: string;
  flags: string[];
} | null {
  if (!raw || !raw.startsWith("Two-Stage")) return null;
  const flagsIdx = raw.lastIndexOf("| flags=");
  const flags = flagsIdx >= 0
    ? raw.slice(flagsIdx + "| flags=".length).split(",").map((s) => s.trim()).filter(Boolean)
    : [];
  const bodyEnd = flagsIdx >= 0 ? flagsIdx : raw.length;
  const body = raw.slice("Two-Stage ".length, bodyEnd).trim();
  const colon = body.indexOf(":");
  const verdict = colon > 0 ? body.slice(0, colon).trim() : "verified";
  const reason = colon > 0 ? body.slice(colon + 1).trim() : body;
  return { verdict, reason, flags };
}

const FLAG_LABELS: Record<string, string> = {
  adjusted_not_reported: "Adjusted, not IFRS",
  per_share_confusion: "Per-share vs total",
  unit_scale_error: "Unit scale",
  currency_error: "Currency",
  continuing_vs_discontinued: "Continuing vs discontinued",
  qsum_mismatch: "Q-sum ≠ FY",
  dual_class_confusion: "Dual-class shares",
  source_quote_does_not_support: "Source quote doesn't support",
};

export function ValueCellPopover({ cell, displayValue, onClose, anchorRect, companyId, onSaved }: Props) {
  const ref = useRef<HTMLDivElement>(null);
  const [expanded, setExpanded] = useState(false);
  const [editing, setEditing] = useState(false);
  const [editValue, setEditValue] = useState("");
  const [saving, setSaving] = useState(false);

  const canEdit = !!(
    companyId
    && cell.value_key
    && cell.period_type
    && cell.period_year != null
    && cell.primary_method !== "calculated"
  );

  const handleStartEdit = () => {
    setEditValue(cell.value != null ? String(cell.value) : "");
    setEditing(true);
  };

  const handleSave = async () => {
    if (!companyId || !cell.value_key || !cell.period_type || cell.period_year == null) return;
    const num = parseFloat(editValue.replace(/,/g, "."));
    if (isNaN(num)) {
      toast.error("Invalid number");
      return;
    }
    setSaving(true);
    try {
      await overrideValue(
        companyId,
        cell.value_key,
        { numeric_value: num, source_name: "Manual" },
        cell.period_type,
        cell.period_year ?? undefined,
      );
      toast.success(`${cell.period_label ?? "Value"} overridden`);
      onSaved?.();
      onClose();
    } catch (e) {
      const detail = (e as { message?: string })?.message;
      toast.error(detail || "Save failed");
    } finally {
      setSaving(false);
    }
  };

  useEffect(() => {
    const onClick = (e: MouseEvent) => {
      if (ref.current && !ref.current.contains(e.target as Node)) onClose();
    };
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose();
    };
    document.addEventListener("mousedown", onClick);
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("mousedown", onClick);
      document.removeEventListener("keydown", onKey);
    };
  }, [onClose]);

  const meta = cellBadge(cell);
  const origin = methodOrigin(cell.primary_method);
  const twoStage = useMemo(() => parseTwoStageSource(cell.source_name), [cell.source_name]);
  const { head, parts } = useMemo(() => parseSource(cell.source_name), [cell.source_name]);

  if (!anchorRect) return null;
  const popoverWidth = 300;
  const popoverHeight = expanded ? 320 : 200;
  let top = anchorRect.bottom + 6;
  if (top + popoverHeight > window.innerHeight - 8) {
    top = Math.max(8, anchorRect.top - popoverHeight - 6);
  }
  const left = Math.min(
    Math.max(anchorRect.right - popoverWidth, 8),
    window.innerWidth - popoverWidth - 8,
  );

  return (
    <div
      ref={ref}
      style={{ top, left, width: popoverWidth }}
      className="fixed z-50 overflow-hidden rounded-xl border border-border/70 bg-popover text-foreground shadow-xl ring-1 ring-black/5"
      onClick={(e) => e.stopPropagation()}
    >
      {/* Header row: method dot + label + period, close on right */}
      <div className="flex items-center justify-between gap-2 px-3 py-2">
        <div className="flex min-w-0 items-center gap-1.5">
          <span className={`h-1.5 w-1.5 shrink-0 rounded-full ${meta.dot}`} />
          <span className={`text-[10.5px] font-semibold uppercase tracking-wider ${meta.text_cls}`}>
            {meta.text}
          </span>
          {origin && (
            <span className="text-[10.5px] text-muted-foreground">
              {origin}
            </span>
          )}
          {cell.manually_overridden && meta.text !== "Manual" && (
            <span className="rounded-full bg-amber-100 px-1.5 py-0.5 text-[9.5px] font-semibold uppercase tracking-wide text-amber-800">
              Override
            </span>
          )}
          <span className="text-[10.5px] text-muted-foreground/70">·</span>
          <span className="truncate text-[11px] text-muted-foreground">{cell.period_label ?? ""}</span>
        </div>
        <button
          onClick={onClose}
          className="rounded p-0.5 text-muted-foreground/70 hover:bg-muted hover:text-foreground"
          aria-label="Close"
        >
          <X className="h-3 w-3" />
        </button>
      </div>

      {/* Big value */}
      <div className="flex items-end justify-between gap-2 border-b border-border/40 px-3 pb-2.5">
        {editing ? (
          <input
            autoFocus
            type="text"
            className="w-full rounded border border-primary bg-background px-1.5 py-1 font-mono text-sm text-foreground outline-none"
            value={editValue}
            onChange={(e) => setEditValue(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter") handleSave();
              if (e.key === "Escape") setEditing(false);
            }}
            disabled={saving}
          />
        ) : (
          <div className="text-[18px] font-semibold tabular-nums leading-none">{displayValue}</div>
        )}
        {canEdit && !editing && (
          <button
            onClick={handleStartEdit}
            className="rounded p-1 text-muted-foreground/70 hover:bg-muted hover:text-foreground"
            aria-label="Edit value"
            title="Override this value manually"
          >
            <Pencil className="h-3 w-3" />
          </button>
        )}
        {editing && (
          <div className="flex gap-1">
            <button
              onClick={handleSave}
              disabled={saving}
              className="rounded bg-primary px-2 py-1 text-[10px] font-semibold uppercase tracking-wider text-primary-foreground hover:bg-primary/90 disabled:opacity-50"
            >
              {saving ? "…" : "Save"}
            </button>
            <button
              onClick={() => setEditing(false)}
              disabled={saving}
              className="rounded border border-border px-2 py-1 text-[10px] font-semibold uppercase tracking-wider text-muted-foreground hover:bg-muted"
            >
              Cancel
            </button>
          </div>
        )}
      </div>

      <div className="px-3 py-2 text-[11.5px] leading-snug space-y-1.5">
        {cell.formula && (
          <div className="flex gap-1.5">
            <span className="shrink-0 font-mono text-muted-foreground">=</span>
            <span className="font-mono text-foreground">{cell.formula}</span>
          </div>
        )}

        {twoStage ? (
          <div className="rounded-md border border-emerald-200 bg-emerald-50/60 p-2 space-y-1.5">
            <div className="flex items-center gap-1.5">
              <span className="text-[10px] font-semibold uppercase tracking-wider text-emerald-800">
                Verifier reasoning
              </span>
              <span className="text-[10px] text-emerald-600/70">·</span>
              <span className="text-[10px] font-medium uppercase text-emerald-700">
                {twoStage.verdict.replace("two_stage_", "").replace("_", " ")}
              </span>
            </div>
            <div className="text-[11.5px] leading-snug text-emerald-950/90">
              {twoStage.reason}
            </div>
            {twoStage.flags.length > 0 && (
              <div className="flex flex-wrap gap-1 pt-0.5">
                {twoStage.flags.map((f) => (
                  <span
                    key={f}
                    className="rounded-full bg-amber-100 px-1.5 py-0.5 text-[9.5px] font-medium text-amber-900"
                    title={f}
                  >
                    {FLAG_LABELS[f] ?? f}
                  </span>
                ))}
              </div>
            )}
          </div>
        ) : head && (
          <div className="text-foreground">{head}</div>
        )}

        {parts.length > 0 && (
          <div>
            <button
              onClick={() => setExpanded((v) => !v)}
              className="text-[10.5px] font-medium text-muted-foreground hover:text-foreground"
            >
              {expanded ? "Hide breakdown" : `Show breakdown (${parts.length})`}
            </button>
            {expanded && (
              <ul className="mt-1 space-y-0.5 rounded-md bg-muted/40 px-2 py-1.5">
                {parts.map((p, i) => (
                  <li key={i} className="font-mono text-[10.5px] leading-snug text-muted-foreground">
                    {p}
                  </li>
                ))}
              </ul>
            )}
          </div>
        )}

        <div className="flex items-center justify-between gap-2 pt-1 text-[10.5px] text-muted-foreground">
          {cell.source_link ? (
            <a
              href={cell.source_link}
              target="_blank"
              rel="noopener noreferrer"
              className="inline-flex items-center gap-1 font-medium text-primary hover:underline"
            >
              <ExternalLink className="h-3 w-3" />
              Open source
            </a>
          ) : (
            <span />
          )}
          {cell.fetched_at && (
            <span title={formatAbsolute(cell.fetched_at)}>{formatRelative(cell.fetched_at)}</span>
          )}
        </div>
      </div>
    </div>
  );
}

// Color-coding for a rendered cell's number.
// Primary marker: is_forecast (Actual vs Estimate). primary_method is only
// used as a secondary discriminator for calculated/manual overrides.
export function cellColorClass(cell: Cell | undefined): string {
  if (!cell || cell.value === null) return "text-muted-foreground/60";
  if (cell.is_gaap_fallback) return "text-muted-foreground/70";
  if (cell.manually_overridden || cell.primary_method === "manual") return "text-amber-700";
  if (cell.primary_method === "calculated") return "text-violet-700";
  if (cell.is_forecast) return "text-sky-700";
  return "text-foreground";
}
