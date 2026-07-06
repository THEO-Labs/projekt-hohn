import { useState, type ReactNode } from "react";
import { ChevronDown } from "lucide-react";

import { Card } from "@/components/ui/card";

type Props = {
  id?: string;
  title: ReactNode;
  right?: ReactNode;
  defaultOpen?: boolean;
  children: ReactNode;
};

export function CollapsibleCard({ id, title, right, defaultOpen = true, children }: Props) {
  const [open, setOpen] = useState(defaultOpen);

  return (
    <Card className="gap-0 py-0 scroll-mt-6" id={id}>
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        className="flex w-full items-center justify-between gap-3 px-6 py-2.5 text-left transition-colors hover:bg-muted/40"
        aria-expanded={open}
      >
        <div className="flex min-w-0 items-center gap-2">
          <ChevronDown
            className={`h-3.5 w-3.5 text-muted-foreground transition-transform ${open ? "" : "-rotate-90"}`}
          />
          <div className="truncate text-[13px] font-semibold text-foreground">{title}</div>
        </div>
        {right && <div className="flex items-center gap-2">{right}</div>}
      </button>
      {open && <div className="border-t border-border/40">{children}</div>}
    </Card>
  );
}
