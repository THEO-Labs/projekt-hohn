import { Button } from "@/components/ui/button";
import { t } from "@/lib/i18n";

type Props = { email: string; onLogout: () => void; backHref?: string; title?: string };

export function AppHeader({ email, onLogout, title }: Props) {
  return (
    <header className="flex items-center justify-between border-b bg-white px-6 py-3">
      <h1 className="text-lg font-semibold">{title ?? t.appTitle}</h1>
      <div className="flex items-center gap-3 text-sm">
        <span className="text-slate-600">{email}</span>
        <Button variant="outline" size="sm" onClick={onLogout}>{t.logout}</Button>
      </div>
    </header>
  );
}
