import { useEffect, useMemo, useState } from "react";
import { Link, useParams } from "react-router-dom";
import { Building2, ChevronLeft, Loader2, Plus, RefreshCw, ShieldCheck } from "lucide-react";
import { toast } from "sonner";

import { AppHeader } from "@/components/AppHeader";
import { CompanyCard } from "@/components/CompanyCard";
import { Button } from "@/components/ui/button";
import { Dialog, DialogContent, DialogHeader, DialogTitle, DialogTrigger } from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { useAuth } from "@/hooks/useAuth";
import {
  createCompany,
  listCompanies,
  lookupCompany,
  type Company,
} from "@/api/companies";
import { twoStageRefresh, type ValueDefinition } from "@/api/values";
import {
  loadCompanyCard,
  getDashboardDefinitions,
  refreshCompanyDaily,
  type CompanyCardData,
} from "@/api/dashboard";

const TWO_STAGE_KEYS = [
  "revenue", "net_income", "ebitda", "fcf", "operating_cash_flow", "capex",
  "sbc", "buyback_volume", "dividends", "cash_and_equivalents",
  "st_debt", "lt_debt", "net_debt",
];
import { t } from "@/lib/i18n";

export function PortfolioDetailPage() {
  const { pid: id } = useParams<{ pid: string }>();
  const { user, logout } = useAuth();

  const [loading, setLoading] = useState(true);
  const [cards, setCards] = useState<CompanyCardData[]>([]);
  const [definitions, setDefinitions] = useState<ValueDefinition[]>([]);

  const [open, setOpen] = useState(false);
  const [form, setForm] = useState({ name: "", ticker: "", isin: "", currency: "EUR" });
  const [lookupQuery, setLookupQuery] = useState("");
  const [lookupLoading, setLookupLoading] = useState(false);
  const [lookedUp, setLookedUp] = useState(false);
  const [submitting, setSubmitting] = useState(false);

  const [refreshingAll, setRefreshingAll] = useState(false);
  const [refreshProgress, setRefreshProgress] = useState<{ done: number; total: number } | null>(null);
  const [verifyingAll, setVerifyingAll] = useState(false);
  const [verifyProgress, setVerifyProgress] = useState<{ done: number; total: number; cost: number } | null>(null);

  const loadAll = async () => {
    if (!id) return;
    setLoading(true);
    try {
      const [companies, defs] = await Promise.all([
        listCompanies(id),
        getDashboardDefinitions().catch(() => [] as ValueDefinition[]),
      ]);
      setDefinitions(defs);
      const cardData = await Promise.all(companies.map((c) => loadCompanyCard(c)));
      setCards(cardData);
    } catch (err) {
      console.error("Portfolio load failed", err);
      toast.error("Failed to load portfolio");
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    loadAll();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [id]);

  const handleTwoStageRefreshAll = async () => {
    if (verifyingAll || cards.length === 0) return;
    const year = new Date().getFullYear() - 1; // Last completed FY
    if (!window.confirm(
      `Two-stage verification will run Extractor + Verifier for ${cards.length} companies × ${TWO_STAGE_KEYS.length} keys for FY ${year}. ` +
      `Estimated cost ~$${(cards.length * TWO_STAGE_KEYS.length * 0.11).toFixed(0)}. Continue?`
    )) return;
    setVerifyingAll(true);
    setVerifyProgress({ done: 0, total: cards.length, cost: 0 });
    const toastId = toast.loading(`Two-stage verification for ${cards.length} companies…`);
    let ok = 0, corrected = 0, insufficient = 0, failed = 0;
    let totalSpent = 0;
    for (const [i, card] of cards.entries()) {
      try {
        const res = await twoStageRefresh(card.company.id, TWO_STAGE_KEYS, year, 5.0);
        totalSpent += res.spent_usd;
        for (const r of res.results) {
          if (r.verdict === "confirm") ok += 1;
          else if (r.verdict === "correct") corrected += 1;
          else if (r.verdict === "insufficient_evidence") insufficient += 1;
          else failed += 1;
        }
      } catch {
        failed += TWO_STAGE_KEYS.length;
      }
      setVerifyProgress({ done: i + 1, total: cards.length, cost: totalSpent });
      toast.loading(
        `Verifying (${i + 1}/${cards.length}) · spent $${totalSpent.toFixed(2)}`,
        { id: toastId },
      );
    }
    toast.dismiss(toastId);
    toast.success(
      `Two-stage done: ${ok} confirmed, ${corrected} corrected, ${insufficient} insufficient, ${failed} failed · $${totalSpent.toFixed(2)}`,
    );
    setVerifyingAll(false);
    setVerifyProgress(null);
    await loadAll();
  };

  const handleRefreshAllDaily = async () => {
    if (refreshingAll || cards.length === 0) return;
    setRefreshingAll(true);
    setRefreshProgress({ done: 0, total: cards.length });
    const toastId = toast.loading(`Refreshing daily numbers for ${cards.length} companies…`);
    let ok = 0;
    let failed = 0;
    for (const [i, card] of cards.entries()) {
      try {
        await refreshCompanyDaily(card.company.id);
        ok += 1;
      } catch {
        failed += 1;
      }
      setRefreshProgress({ done: i + 1, total: cards.length });
      toast.loading(`Refreshing daily numbers (${i + 1}/${cards.length})…`, { id: toastId });
    }
    toast.dismiss(toastId);
    if (failed === 0) toast.success(`Daily numbers refreshed for all ${ok} companies`);
    else toast.warning(`Daily refresh: ${ok} ok, ${failed} failed`);
    setRefreshingAll(false);
    setRefreshProgress(null);
    await loadAll();
  };

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
    if (!id || !lookedUp || submitting) return;
    setSubmitting(true);
    try {
      const created = await createCompany(id, {
        name: form.name,
        ticker: form.ticker,
        currency: form.currency,
        isin: form.isin || undefined,
      });
      setForm({ name: "", ticker: "", isin: "", currency: "EUR" });
      setLookupQuery("");
      setLookedUp(false);
      setOpen(false);
      const card = await loadCompanyCard(created);
      setCards((prev) => [...prev, card]);
      toast.success(`${created.ticker} added`);
    } finally {
      setSubmitting(false);
    }
  };

  const onCardChanged = (companyId: string) => (updated: CompanyCardData | null) => {
    if (updated == null) {
      setCards((prev) => prev.filter((c) => c.company.id !== companyId));
    } else {
      setCards((prev) => prev.map((c) => (c.company.id === companyId ? updated : c)));
    }
  };

  const orderedCards = useMemo(
    () => [...cards].sort((a, b) => a.company.name.localeCompare(b.company.name, "de")),
    [cards],
  );

  if (!user) return null;

  return (
    <div className="min-h-screen bg-background">
      <AppHeader email={user.email} onLogout={logout} />
      <main className="mx-auto max-w-6xl px-6 py-10">
        <div className="mb-6">
          <Link
            to="/"
            className="inline-flex items-center gap-1 text-sm text-muted-foreground transition-colors hover:text-foreground"
          >
            <ChevronLeft className="h-3.5 w-3.5" />
            {t.portfolios}
          </Link>
        </div>

        <div className="mb-8 flex items-end justify-between">
          <div>
            <h2 className="text-3xl font-semibold tracking-tight text-foreground">{t.companies}</h2>
            <p className="mt-1 text-sm text-muted-foreground">
              {cards.length === 1 ? "1 company" : `${cards.length} companies`} in this portfolio.
            </p>
          </div>
          <div className="flex items-center gap-2">
            <Button
              variant="outline"
              onClick={handleRefreshAllDaily}
              disabled={refreshingAll || verifyingAll || cards.length === 0}
              title="Refresh daily numbers (Stock Price, Market Cap, Shares) for the entire portfolio — fast, no web research"
              className="flex items-center gap-1.5"
            >
              {refreshingAll
                ? <Loader2 className="h-4 w-4 animate-spin" />
                : <RefreshCw className="h-4 w-4" />}
              {refreshingAll && refreshProgress
                ? `Refreshing ${refreshProgress.done}/${refreshProgress.total}…`
                : "Refresh daily (all)"}
            </Button>
            <Button
              variant="outline"
              onClick={handleTwoStageRefreshAll}
              disabled={refreshingAll || verifyingAll || cards.length === 0}
              title="Two-stage extractor + verifier: fetches values from official reports and challenges each with a separate LLM pass. Slower and more expensive than daily refresh but catches per-share/adjusted/unit-scale errors."
              className="flex items-center gap-1.5"
            >
              {verifyingAll
                ? <Loader2 className="h-4 w-4 animate-spin" />
                : <ShieldCheck className="h-4 w-4" />}
              {verifyingAll && verifyProgress
                ? `Verifying ${verifyProgress.done}/${verifyProgress.total} · $${verifyProgress.cost.toFixed(2)}`
                : "Verify (2-stage, all)"}
            </Button>
          <Dialog open={open} onOpenChange={(v) => { setOpen(v); if (!v) { setLookupQuery(""); setLookedUp(false); } }}>
            <DialogTrigger render={<Button className="flex items-center gap-1.5 shadow-lg shadow-primary/20" />}>
              <Plus className="h-4 w-4" />
              {t.newCompany}
            </DialogTrigger>
            <DialogContent>
              <DialogHeader>
                <DialogTitle>{t.newCompany}</DialogTitle>
              </DialogHeader>
              <form onSubmit={submit} className="space-y-4">
                <div className="space-y-1.5">
                  <Label className="text-sm text-muted-foreground">Enter ISIN or ticker</Label>
                  <div className="flex gap-2">
                    <Input
                      value={lookupQuery}
                      onChange={(e) => setLookupQuery(e.target.value.toUpperCase())}
                      placeholder="e.g. US02079K3059 or GOOGL"
                      className="font-mono"
                      onKeyDown={(e) => { if (e.key === "Enter") { e.preventDefault(); handleLookup(); } }}
                    />
                    <Button type="button" variant="secondary" onClick={handleLookup} disabled={lookupLoading || !lookupQuery.trim()}>
                      {lookupLoading ? "..." : t.lookup}
                    </Button>
                  </div>
                </div>

                {lookedUp && (
                  <div className="space-y-3">
                    <div className="rounded-lg border border-green-200 bg-green-50 px-3 py-2">
                      <p className="text-xs font-medium text-green-800">Company found</p>
                    </div>
                    <div className="rounded-lg border border-border bg-muted/30 px-4 py-3 space-y-2">
                      {[
                        ["Name", form.name],
                        ["Ticker", form.ticker],
                        ["ISIN", form.isin],
                        ["Currency", form.currency],
                      ].map(([k, v]) => (
                        <div key={k} className="flex justify-between text-sm">
                          <span className="text-muted-foreground">{k}</span>
                          <span className="font-mono font-medium text-foreground">{v}</span>
                        </div>
                      ))}
                    </div>
                  </div>
                )}
                <div className="flex justify-end gap-2">
                  <Button type="button" variant="ghost" onClick={() => setOpen(false)}>{t.cancel}</Button>
                  <Button type="submit" disabled={!lookedUp || submitting}>{t.save}</Button>
                </div>
              </form>
            </DialogContent>
          </Dialog>
          </div>
        </div>

        {loading ? (
          <div className="flex items-center justify-center py-24 text-muted-foreground">
            <Loader2 className="mr-2 h-4 w-4 animate-spin" /> Loading portfolio…
          </div>
        ) : orderedCards.length === 0 ? (
          <div className="flex flex-col items-center justify-center rounded-xl border border-dashed border-border/60 bg-card/30 px-8 py-20 text-center">
            <div className="mb-4 flex h-12 w-12 items-center justify-center rounded-xl bg-muted/60">
              <Building2 className="h-6 w-6 text-muted-foreground" />
            </div>
            <p className="mb-1 text-sm font-medium text-foreground">No companies yet</p>
            <p className="text-xs text-muted-foreground">
              Add your first company using the button in the top right.
            </p>
          </div>
        ) : (
          <div className="grid grid-cols-1 gap-4">
            {orderedCards.map((c) => (
              <CompanyCard
                key={c.company.id}
                portfolioId={id!}
                data={c}
                definitions={definitions}
                onChanged={onCardChanged(c.company.id)}
              />
            ))}
          </div>
        )}
      </main>
    </div>
  );
}

// Legacy: some existing imports still reference the old Company type re-export.
export type { Company };
