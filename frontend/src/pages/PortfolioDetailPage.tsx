import { useEffect, useState } from "react";
import { Link, useParams } from "react-router-dom";
import { ChevronLeft, Plus, Building2, Trash2 } from "lucide-react";
import { toast } from "sonner";

import { AppHeader } from "@/components/AppHeader";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Dialog, DialogContent, DialogHeader, DialogTitle, DialogTrigger } from "@/components/ui/dialog";
import { useAuth } from "@/hooks/useAuth";
import {
  createCompany,
  deleteCompany,
  listCompanies,
  lookupCompany,
  type Company,
} from "@/api/companies";
import { t } from "@/lib/i18n";

export function PortfolioDetailPage() {
  const { pid: id } = useParams<{ pid: string }>();
  const { user, logout } = useAuth();
  const [companies, setCompanies] = useState<Company[]>([]);
  const [open, setOpen] = useState(false);
  const [form, setForm] = useState({ name: "", ticker: "", isin: "", currency: "EUR" });
  const [lookupQuery, setLookupQuery] = useState("");
  const [lookupLoading, setLookupLoading] = useState(false);
  const [lookedUp, setLookedUp] = useState(false);

  const refresh = () => {
    if (id) listCompanies(id).then(setCompanies);
  };

  useEffect(() => {
    refresh();
  }, [id]);


  const handleLookup = async () => {
    const q = lookupQuery.trim();
    if (!q) return;
    setLookupLoading(true);
    try {
      const isIsin = /^[A-Z]{2}/.test(q) && q.length === 12;
      const result = isIsin
        ? await lookupCompany({ isin: q })
        : await lookupCompany({ ticker: q });

      if (!result.name && !result.ticker && !result.isin && !result.currency) {
        toast.info(t.lookupNotFound);
      } else {
        setForm((prev) => ({
          name: result.name ?? prev.name,
          ticker: result.ticker ?? prev.ticker,
          isin: result.isin ?? prev.isin,
          currency: result.currency ?? prev.currency,
        }));
        setLookedUp(true);
      }
    } catch {
      toast.error(t.lookupError);
    } finally {
      setLookupLoading(false);
    }
  };

  const submit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!id || !lookedUp) return;
    await createCompany(id, {
      name: form.name,
      ticker: form.ticker,
      currency: form.currency,
      isin: form.isin || undefined,
    });
    setForm({ name: "", ticker: "", isin: "", currency: "EUR" });
    setLookupQuery("");
    setLookedUp(false);
    setOpen(false);
    refresh();
  };

  const remove = async (cid: string) => {
    await deleteCompany(cid);
    refresh();
  };

  if (!user) return null;

  return (
    <div className="min-h-screen bg-background">
      <AppHeader email={user.email} onLogout={logout} />
      <main className="mx-auto max-w-6xl px-6 py-10">
        {/* Breadcrumb back nav */}
        <div className="mb-6">
          <Link
            to={`/portfolios/${id}`}
            className="inline-flex items-center gap-1 text-sm text-muted-foreground transition-colors hover:text-foreground"
          >
            <ChevronLeft className="h-3.5 w-3.5" />
            {t.dashboard}
          </Link>
        </div>

        {/* Page header */}
        <div className="mb-8 flex items-end justify-between">
          <div>
            <h2 className="text-3xl font-semibold tracking-tight text-foreground">{t.companies}</h2>
            <p className="mt-1 text-sm text-muted-foreground">
              Unternehmen in diesem Portfolio verwalten.
            </p>
          </div>
          <Dialog open={open} onOpenChange={(v) => { setOpen(v); if (!v) { setLookupQuery(""); } }}>
            <DialogTrigger render={
              <Button className="flex items-center gap-1.5 shadow-lg shadow-primary/20 transition-all hover:shadow-primary/30" />
            }>
              <Plus className="h-4 w-4" />
              {t.newCompany}
            </DialogTrigger>
            <DialogContent>
              <DialogHeader>
                <DialogTitle>{t.newCompany}</DialogTitle>
              </DialogHeader>
              <form onSubmit={submit} className="space-y-4">
                <div className="space-y-1.5">
                  <Label className="text-sm text-muted-foreground">ISIN eingeben</Label>
                  <div className="flex gap-2">
                    <Input
                      value={lookupQuery}
                      onChange={(e) => setLookupQuery(e.target.value.toUpperCase())}
                      placeholder="z.B. US92826C8394"
                      className="font-mono"
                      onKeyDown={(e) => { if (e.key === "Enter") { e.preventDefault(); handleLookup(); } }}
                    />
                    <Button
                      type="button"
                      variant="secondary"
                      onClick={handleLookup}
                      disabled={lookupLoading || !lookupQuery.trim()}
                    >
                      {lookupLoading ? "..." : t.lookup}
                    </Button>
                  </div>
                </div>

                {lookedUp && (
                  <div className="space-y-3">
                    <div className="rounded-lg border border-green-200 bg-green-50 px-3 py-2">
                      <p className="text-xs font-medium text-green-800">Firma gefunden</p>
                    </div>
                    <div className="rounded-lg border border-border bg-muted/30 px-4 py-3 space-y-2">
                      <div className="flex justify-between text-sm">
                        <span className="text-muted-foreground">Name</span>
                        <span className="font-medium text-foreground">{form.name}</span>
                      </div>
                      <div className="flex justify-between text-sm">
                        <span className="text-muted-foreground">Ticker</span>
                        <span className="font-mono font-medium text-foreground">{form.ticker}</span>
                      </div>
                      <div className="flex justify-between text-sm">
                        <span className="text-muted-foreground">ISIN</span>
                        <span className="font-mono font-medium text-foreground">{form.isin}</span>
                      </div>
                      <div className="flex justify-between text-sm">
                        <span className="text-muted-foreground">Currency</span>
                        <span className="font-mono font-medium text-foreground">{form.currency}</span>
                      </div>
                    </div>
                  </div>
                )}

                <div className="flex justify-end gap-2">
                  <Button type="button" variant="ghost" onClick={() => setOpen(false)}>{t.cancel}</Button>
                  <Button type="submit" disabled={!lookedUp}>{t.save}</Button>
                </div>
              </form>
            </DialogContent>
          </Dialog>
        </div>

        {companies.length === 0 ? (
          <div className="flex flex-col items-center justify-center rounded-xl border border-dashed border-border/60 bg-card/30 px-8 py-20 text-center">
            <div className="mb-4 flex h-12 w-12 items-center justify-center rounded-xl bg-muted/60">
              <Building2 className="h-6 w-6 text-muted-foreground" />
            </div>
            <p className="mb-1 text-sm font-medium text-foreground">Noch keine Firmen</p>
            <p className="text-xs text-muted-foreground">
              Füge die erste Firma mit dem Button oben rechts hinzu.
            </p>
          </div>
        ) : (
          <div className="overflow-hidden rounded-xl border border-border/60 bg-card">
            {companies.map((c, i) => (
              <div
                key={c.id}
                className={`group flex items-center justify-between px-5 py-4 transition-colors hover:bg-muted/30 ${
                  i > 0 ? "border-t border-border/40" : ""
                }`}
              >
                <div className="flex items-center gap-4">
                  <div className="flex h-8 w-8 shrink-0 items-center justify-center rounded-lg bg-muted/60">
                    <Building2 className="h-4 w-4 text-muted-foreground" />
                  </div>
                  <div>
                    <Link to={`/portfolios/${id}/companies/${c.id}`} className="font-medium text-foreground hover:text-primary transition-colors hover:underline">{c.name}</Link>
                    <div className="mt-0.5 flex items-center gap-2">
                      <span className="tabular rounded bg-primary/10 px-1.5 py-0.5 font-mono text-[11px] font-medium text-primary">
                        {c.ticker}
                      </span>
                      <span className="rounded bg-muted px-1.5 py-0.5 text-[11px] font-medium text-muted-foreground">
                        {c.currency}
                      </span>
                    </div>
                  </div>
                </div>
                <Button
                  variant="ghost"
                  size="sm"
                  onClick={() => remove(c.id)}
                  className="h-8 w-8 p-0 text-muted-foreground opacity-0 transition-all group-hover:opacity-100 hover:text-destructive"
                >
                  <Trash2 className="h-3.5 w-3.5" />
                </Button>
              </div>
            ))}
          </div>
        )}
      </main>
    </div>
  );
}
