import { LogOut } from "lucide-react";
import { Button } from "@/components/ui/button";
import { t } from "@/lib/i18n";

type Props = { email: string; onLogout: () => void; backHref?: string; title?: string };

export function AppHeader({ email, onLogout }: Props) {
  return (
    <header className="sticky top-0 z-50 flex items-center justify-between border-b border-border/60 bg-background/80 px-6 py-4 backdrop-blur-xl">
      <img src="/logo.svg" alt="Turning Point Investments" className="h-7 w-auto" />

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
