import { TrendingUp, LogOut } from "lucide-react";
import { Button } from "@/components/ui/button";
import { t } from "@/lib/i18n";

type Props = { email: string; onLogout: () => void; backHref?: string; title?: string };

export function AppHeader({ email, onLogout }: Props) {
  return (
    <header className="sticky top-0 z-50 flex items-center justify-between border-b border-border/60 bg-background/80 px-6 py-3 backdrop-blur-xl">
      <div className="flex items-center gap-2.5">
        <div className="flex h-7 w-7 items-center justify-center rounded-md bg-primary/15">
          <TrendingUp className="h-4 w-4 text-primary" strokeWidth={2.5} />
        </div>
        <div className="flex flex-col leading-none">
          <span className="text-sm font-semibold tracking-tight text-foreground">Hohn-Rendite</span>
          <span className="text-[10px] font-medium tracking-wide text-muted-foreground uppercase">
            Turning Point Investments
          </span>
        </div>
      </div>

      <div className="flex items-center gap-2">
        <div className="hidden items-center rounded-full border border-border/60 bg-muted/50 px-3 py-1 sm:flex">
          <span className="text-xs text-muted-foreground">{email}</span>
        </div>
        <Button
          variant="ghost"
          size="sm"
          onClick={onLogout}
          className="flex items-center gap-1.5 text-muted-foreground transition-colors hover:text-foreground"
        >
          <LogOut className="h-3.5 w-3.5" />
          <span className="hidden sm:inline">{t.logout}</span>
        </Button>
      </div>
    </header>
  );
}
