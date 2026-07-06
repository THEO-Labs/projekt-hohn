import { useEffect, useRef } from "react";
import { ExternalLink, X } from "lucide-react";

import type { Cell } from "@/pages/companyDetailMocks";
import { formatAbsolute } from "@/lib/format";

type Props = {
  cell: Cell;
  displayValue: string;
  onClose: () => void;
  anchorRect: DOMRect | null;
};

function methodLabel(method: string | null | undefined): { text: string; color: string } {
  switch (method) {
    case "provider":
      return { text: "Actual (Provider)", color: "bg-slate-100 text-slate-800 ring-slate-200" };
    case "pdf":
      return { text: "Actual (PDF)", color: "bg-slate-100 text-slate-800 ring-slate-200" };
    case "manual":
      return { text: "Manual override", color: "bg-amber-100 text-amber-800 ring-amber-200" };
    case "web_guidance":
      return { text: "Estimate (Web Research)", color: "bg-sky-100 text-sky-800 ring-sky-200" };
    case "calculated":
      return { text: "Calculated", color: "bg-violet-100 text-violet-800 ring-violet-200" };
    default:
      return { text: "Unknown source", color: "bg-muted text-muted-foreground ring-border" };
  }
}

export function ValueCellPopover({ cell, displayValue, onClose, anchorRect }: Props) {
  const ref = useRef<HTMLDivElement>(null);

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

  if (!anchorRect) return null;
  // position: fixed is viewport-relative — no scroll offset needed.
  const popoverWidth = 320;
  const popoverHeight = 260; // rough max
  let top = anchorRect.bottom + 6;
  // Flip above the cell if not enough space below.
  if (top + popoverHeight > window.innerHeight - 8) {
    top = Math.max(8, anchorRect.top - popoverHeight - 6);
  }
  const left = Math.min(
    Math.max(anchorRect.right - popoverWidth, 8),
    window.innerWidth - popoverWidth - 8,
  );

  const meta = methodLabel(cell.primary_method);

  return (
    <div
      ref={ref}
      style={{ top, left, width: popoverWidth }}
      className="fixed z-50 rounded-xl border border-border/70 bg-popover p-3 text-[12px] text-foreground shadow-xl ring-1 ring-black/5"
      onClick={(e) => e.stopPropagation()}
    >
      <div className="flex items-start justify-between gap-2">
        <div className="min-w-0">
          <div className="truncate text-[11px] font-medium text-muted-foreground">{cell.period_label ?? "Value"}</div>
          <div className="mt-0.5 text-[15px] font-semibold tabular-nums">{displayValue}</div>
        </div>
        <button
          onClick={onClose}
          className="rounded p-1 text-muted-foreground hover:bg-muted"
          aria-label="Close"
        >
          <X className="h-3.5 w-3.5" />
        </button>
      </div>

      <div className="mt-2 flex flex-wrap items-center gap-1">
        <span className={`rounded-full px-1.5 py-0.5 text-[10px] font-semibold uppercase tracking-wide ring-1 ${meta.color}`}>
          {meta.text}
        </span>
        {cell.manually_overridden && (
          <span className="rounded-full bg-amber-100 px-1.5 py-0.5 text-[10px] font-semibold uppercase tracking-wide text-amber-800 ring-1 ring-amber-200">
            Overridden
          </span>
        )}
      </div>

      {cell.formula && (
        <div className="mt-2 rounded-lg bg-muted/50 px-2 py-1.5">
          <div className="text-[10px] font-semibold uppercase tracking-wide text-muted-foreground">Formula</div>
          <div className="mt-0.5 font-mono text-[11.5px] text-foreground">{cell.formula}</div>
        </div>
      )}

      {cell.source_name && (
        <div className="mt-2">
          <div className="text-[10px] font-semibold uppercase tracking-wide text-muted-foreground">Source</div>
          <div className="mt-0.5 whitespace-pre-wrap break-words text-[11.5px] leading-snug">
            {cell.source_name}
          </div>
        </div>
      )}

      {cell.source_link && (
        <a
          href={cell.source_link}
          target="_blank"
          rel="noopener noreferrer"
          className="mt-2 inline-flex items-center gap-1 text-[11px] font-medium text-primary hover:underline"
        >
          <ExternalLink className="h-3 w-3" />
          Open source
        </a>
      )}

      {cell.fetched_at && (
        <div className="mt-2 text-[10.5px] text-muted-foreground">
          Fetched {formatAbsolute(cell.fetched_at)}
        </div>
      )}
    </div>
  );
}

// Color-coding for a rendered cell's number, based on its primary_method.
export function cellColorClass(cell: Cell | undefined): string {
  if (!cell || cell.value === null) return "text-muted-foreground/60";
  const m = cell.primary_method;
  if (m === "web_guidance") return "text-sky-700";
  if (m === "calculated") return "text-violet-700";
  if (m === "manual") return "text-amber-700";
  // provider / pdf / null(actual) => default foreground
  return "text-foreground";
}
