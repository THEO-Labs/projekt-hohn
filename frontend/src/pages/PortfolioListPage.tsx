import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { Plus, FolderOpen, ChevronRight, Trash2 } from "lucide-react";

import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Dialog, DialogContent, DialogHeader, DialogTitle, DialogTrigger } from "@/components/ui/dialog";
import { AppHeader } from "@/components/AppHeader";
import { useAuth } from "@/hooks/useAuth";
import {
  createPortfolio,
  deletePortfolio,
  listPortfolios,
  type Portfolio,
} from "@/api/portfolios";
import { t } from "@/lib/i18n";

export function PortfolioListPage() {
  const { user, logout } = useAuth();
  const [portfolios, setPortfolios] = useState<Portfolio[]>([]);
  const [open, setOpen] = useState(false);
  const [name, setName] = useState("");
  const [submitting, setSubmitting] = useState(false);

  const refresh = () => listPortfolios().then(setPortfolios);

  useEffect(() => {
    refresh();
  }, []);

  const submit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (submitting) return;
    setSubmitting(true);
    try {
      await createPortfolio(name);
      setName("");
      setOpen(false);
      refresh();
    } finally {
      setSubmitting(false);
    }
  };

  const remove = async (id: string) => {
    if (!window.confirm("Portfolio und alle enthaltenen Firmen wirklich löschen?")) return;
    await deletePortfolio(id);
    refresh();
  };

  if (!user) return null;

  return (
    <div className="min-h-screen bg-background">
      <AppHeader email={user.email} onLogout={logout} />
      <main className="mx-auto max-w-6xl px-6 py-10">
        {/* Hero area */}
        <div className="mb-8 flex items-end justify-between">
          <div>
            <h2 className="text-3xl font-semibold tracking-tight text-foreground">{t.portfolios}</h2>
            <p className="mt-1 text-sm text-muted-foreground">
              Verwalte deine Investmentportfolios und Unternehmenspositionen.
            </p>
          </div>
          <Dialog open={open} onOpenChange={setOpen}>
            <DialogTrigger render={
              <Button className="flex items-center gap-1.5 shadow-lg shadow-primary/20 transition-all hover:shadow-primary/30" />
            }>
              <Plus className="h-4 w-4" />
              {t.newPortfolio}
            </DialogTrigger>
            <DialogContent>
              <DialogHeader>
                <DialogTitle>{t.newPortfolio}</DialogTitle>
              </DialogHeader>
              <form onSubmit={submit} className="space-y-4">
                <div className="space-y-1.5">
                  <Label htmlFor="pname">{t.portfolioName}</Label>
                  <Input id="pname" value={name} onChange={(e) => setName(e.target.value)} required />
                </div>
                <div className="flex justify-end gap-2">
                  <Button type="button" variant="ghost" onClick={() => setOpen(false)}>
                    {t.cancel}
                  </Button>
                  <Button type="submit" disabled={submitting || !name.trim()}>{t.save}</Button>
                </div>
              </form>
            </DialogContent>
          </Dialog>
        </div>

        {portfolios.length === 0 ? (
          <div className="flex flex-col items-center justify-center rounded-xl border border-dashed border-border/60 bg-card/30 px-8 py-20 text-center">
            <div className="mb-4 flex h-12 w-12 items-center justify-center rounded-xl bg-muted/60">
              <FolderOpen className="h-6 w-6 text-muted-foreground" />
            </div>
            <p className="mb-1 text-sm font-medium text-foreground">Noch keine Portfolios</p>
            <p className="text-xs text-muted-foreground">
              Erstelle dein erstes Portfolio mit dem Button oben rechts.
            </p>
          </div>
        ) : (
          <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
            {portfolios.map((p) => (
              <div
                key={p.id}
                className="group relative rounded-xl border border-border/60 bg-card transition-all duration-200 hover:border-border hover:bg-card/80 hover:shadow-lg hover:shadow-black/10"
              >
                <Link
                  to={`/portfolios/${p.id}`}
                  className="flex items-start gap-3 p-5"
                >
                  <div className="mt-0.5 flex h-8 w-8 shrink-0 items-center justify-center rounded-lg bg-primary/10">
                    <FolderOpen className="h-4 w-4 text-primary" />
                  </div>
                  <div className="min-w-0 flex-1">
                    <p className="truncate font-medium text-foreground group-hover:text-primary transition-colors">
                      {p.name}
                    </p>
                    <p className="mt-0.5 text-xs text-muted-foreground">Portfolio</p>
                  </div>
                  <ChevronRight className="mt-0.5 h-4 w-4 shrink-0 text-muted-foreground opacity-0 transition-opacity group-hover:opacity-100" />
                </Link>
                <div className="absolute right-3 top-3 opacity-0 transition-opacity group-hover:opacity-100">
                  <Button
                    variant="ghost"
                    size="sm"
                    onClick={() => remove(p.id)}
                    className="h-7 w-7 p-0 text-muted-foreground hover:text-destructive"
                  >
                    <Trash2 className="h-3.5 w-3.5" />
                  </Button>
                </div>
              </div>
            ))}
          </div>
        )}
      </main>
    </div>
  );
}
