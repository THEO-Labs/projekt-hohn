import { useEffect, useMemo, useState } from "react";
import { Link, useParams } from "react-router-dom";
import { Building2, ChevronLeft, Loader2, Plus, RefreshCw, Share2, X, Zap } from "lucide-react";
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
import type { ValueDefinition } from "@/api/values";
import {
  loadCompanyCard,
  getDashboardDefinitions,
  refreshCompanyDaily,
  sortCompanyCards,
  type CardSortMode,
  type CompanyCardData,
} from "@/api/dashboard";
import {
  startFullRecompute,
  startSmartRecompute,
  getFullRecomputeStatus,
  listMembers,
  addMember,
  removeMember,
  type PortfolioMember,
  type BatchStatus,
} from "@/api/portfolios";
import { ApiError } from "@/api/client";
import { t } from "@/lib/i18n";

const SORT_STORAGE_KEY = "hohn:portfolio_sort";

function loadSortMode(): CardSortMode {
  try {
    const raw = localStorage.getItem(SORT_STORAGE_KEY);
    return raw === "h_return" || raw === "earnings" ? raw : "alphabetical";
  } catch { return "alphabetical"; }
}

function saveSortMode(mode: CardSortMode): void {
  try { localStorage.setItem(SORT_STORAGE_KEY, mode); } catch { /* ignore */ }
}

export function PortfolioDetailPage() {
  const { pid: id } = useParams<{ pid: string }>();
  const { user, logout } = useAuth();

  const [loading, setLoading] = useState(true);
  const [cards, setCards] = useState<CompanyCardData[]>([]);
  const [sortMode, setSortMode] = useState<CardSortMode>(loadSortMode);
  const [definitions, setDefinitions] = useState<ValueDefinition[]>([]);

  const [open, setOpen] = useState(false);
  const [form, setForm] = useState({ name: "", ticker: "", isin: "", currency: "EUR" });
  const [lookupQuery, setLookupQuery] = useState("");
  const [lookupLoading, setLookupLoading] = useState(false);
  const [lookedUp, setLookedUp] = useState(false);
  const [submitting, setSubmitting] = useState(false);

  // Teilen-Dialog: Mitglieder werden erst beim Oeffnen geladen
  const [shareOpen, setShareOpen] = useState(false);
  const [members, setMembers] = useState<PortfolioMember[] | null>(null);
  const [shareEmail, setShareEmail] = useState("");
  const [shareBusy, setShareBusy] = useState(false);

  const [refreshingAll, setRefreshingAll] = useState(false);
  const [batch, setBatch] = useState<BatchStatus | null>(null);

  useEffect(() => {
    if (!id) return;
    let timer: number | null = null;
    const pollBatch = async () => {
      try {
        const s = await getFullRecomputeStatus(id);
        setBatch(s.status === "idle" ? null : s);
        if (s.status === "done" && timer !== null) {
          window.clearInterval(timer);
          timer = null;
        }
      } catch { /* backend nicht erreichbar */ }
    };
    pollBatch();
    timer = window.setInterval(pollBatch, 5000);
    return () => { if (timer !== null) window.clearInterval(timer); };
  }, [id]);

  const handleFullRecomputeAll = async () => {
    if (!id) return;
    if (!window.confirm("Full Recompute fuer ALLE Firmen starten? Dauer mehrere Stunden, LLM-Kosten fallen an.")) return;
    const s = await startFullRecompute(id);
    setBatch(s);
    toast.success(`Full recompute gestartet (${s.total} Firmen, 3 parallel)`);
  };

  const handleSmartRecompute = async () => {
    if (!id) return;
    if (!window.confirm("Smart Recompute starten? Rechnet nur Firmen mit neuen Earnings seit dem letzten Recompute.")) return;
    const s = await startSmartRecompute(id);
    // Kein selected-Feld = der Guard hat einen bereits laufenden Batch
    // zurueckgegeben (Smart oder Full) — nicht als Neustart melden.
    if (s.selected == null) {
      setBatch(s);
      toast.info("Es laeuft bereits ein Recompute fuer dieses Portfolio");
      return;
    }
    if (s.selected.length === 0 && s.status === "done") {
      toast.info("Keine Firma hat neue Earnings — nichts zu tun");
      return;
    }
    setBatch(s);
    const shown = s.selected.slice(0, 5).join(", ");
    const more = s.selected.length > 5 ? ` +${s.selected.length - 5} weitere` : "";
    toast.success(`Smart recompute gestartet (${s.total} Firmen: ${shown}${more})`);
  };
  const [refreshProgress, setRefreshProgress] = useState<{ done: number; total: number } | null>(null);

  // Owner-Check ueber die members-Liste: nur der Owner darf verwalten
  const isOwner = members?.some((m) => m.user_id === user?.id && m.is_owner) ?? false;

  const loadMembers = async () => {
    if (!id) return;
    try {
      setMembers(await listMembers(id));
    } catch {
      toast.error("Mitglieder konnten nicht geladen werden");
    }
  };

  const openShareDialog = (v: boolean) => {
    setShareOpen(v);
    if (v) loadMembers();
    else { setMembers(null); setShareEmail(""); }
  };

  const handleAddMember = async (e: React.FormEvent) => {
    e.preventDefault();
    const email = shareEmail.trim();
    if (!id || !email || shareBusy) return;
    setShareBusy(true);
    try {
      await addMember(id, email);
      setShareEmail("");
      toast.success(`${email} hinzugefuegt`);
      await loadMembers();
    } catch (err) {
      toast.error(err instanceof ApiError ? err.message : "Hinzufuegen fehlgeschlagen");
    } finally {
      setShareBusy(false);
    }
  };

  const handleRemoveMember = async (m: PortfolioMember) => {
    if (!id || shareBusy) return;
    setShareBusy(true);
    try {
      await removeMember(id, m.user_id);
      toast.success(`${m.email} entfernt`);
      await loadMembers();
    } catch (err) {
      toast.error(err instanceof ApiError ? err.message : "Entfernen fehlgeschlagen");
    } finally {
      setShareBusy(false);
    }
  };

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

  const changeSortMode = (mode: CardSortMode) => {
    setSortMode(mode);
    saveSortMode(mode);
  };

  // Sortierung nur beim Rendern — der cards-State bleibt unangetastet,
  // damit onChanged-Updates einzelner Karten weiter per id greifen.
  const orderedCards = useMemo(() => sortCompanyCards(cards, sortMode), [cards, sortMode]);

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
          <div className="flex flex-wrap items-center gap-2">
            {/* Sortier-Auswahl: kompaktes Segmented-Control, Wahl landet in localStorage */}
            <div
              role="group"
              aria-label="Sortierung"
              className="flex items-center gap-0.5 rounded-lg border border-border p-0.5"
            >
              {([
                ["alphabetical", "Alphabetisch"],
                ["h_return", "H-Return"],
                ["earnings", "Earnings"],
              ] as const).map(([mode, label]) => (
                <Button
                  key={mode}
                  size="sm"
                  variant={sortMode === mode ? "secondary" : "ghost"}
                  aria-pressed={sortMode === mode}
                  onClick={() => changeSortMode(mode)}
                >
                  {label}
                </Button>
              ))}
            </div>
            <Button
              variant="outline"
              onClick={handleRefreshAllDaily}
              disabled={refreshingAll || cards.length === 0}
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
              onClick={handleFullRecomputeAll}
              disabled={batch?.status === "running" || cards.length === 0}
              title="Full Recompute (Two-Stage-Research) fuer alle Firmen — Queue mit 3 parallel, dauert Stunden"
              className="flex items-center gap-1.5"
            >
              {batch?.status === "running"
                ? <Loader2 className="h-4 w-4 animate-spin" />
                : <RefreshCw className="h-4 w-4" />}
              {batch?.status === "running"
                ? `Recompute ${batch.done ?? 0}/${batch.total ?? 0} (${(batch.current ?? []).join(", ")})`
                : "Full recompute (all)"}
            </Button>
            <Button
              variant="outline"
              onClick={handleSmartRecompute}
              disabled={batch?.status === "running" || cards.length === 0}
              title="Smart Recompute — rechnet nur Firmen mit neuen Earnings seit dem letzten Recompute"
              className="flex items-center gap-1.5"
            >
              <Zap className="h-4 w-4" />
              Smart recompute
            </Button>
          <Dialog open={shareOpen} onOpenChange={openShareDialog}>
            <DialogTrigger render={<Button variant="outline" className="flex items-center gap-1.5" />}>
              <Share2 className="h-4 w-4" />
              Teilen
            </DialogTrigger>
            <DialogContent>
              <DialogHeader>
                <DialogTitle>Portfolio teilen</DialogTitle>
              </DialogHeader>
              {members === null ? (
                <div className="flex items-center justify-center py-6 text-muted-foreground">
                  <Loader2 className="mr-2 h-4 w-4 animate-spin" /> Lade Mitglieder…
                </div>
              ) : (
                <div className="space-y-4">
                  <div className="space-y-1">
                    {members.map((m) => (
                      <div
                        key={m.user_id}
                        className="flex items-center justify-between rounded-lg border border-border px-3 py-2 text-sm"
                      >
                        <span className="font-mono text-foreground">{m.email}</span>
                        {m.is_owner ? (
                          <span className="text-xs text-muted-foreground">Owner</span>
                        ) : isOwner ? (
                          <Button
                            type="button"
                            size="sm"
                            variant="ghost"
                            disabled={shareBusy}
                            onClick={() => handleRemoveMember(m)}
                            title={`${m.email} entfernen`}
                          >
                            <X className="h-4 w-4" />
                          </Button>
                        ) : null}
                      </div>
                    ))}
                  </div>
                  {isOwner && (
                    <form onSubmit={handleAddMember} className="space-y-1.5">
                      <Label className="text-sm text-muted-foreground">
                        Per E-Mail hinzufuegen (User muss bereits existieren)
                      </Label>
                      <div className="flex gap-2">
                        <Input
                          type="email"
                          value={shareEmail}
                          onChange={(e) => setShareEmail(e.target.value)}
                          placeholder="name@example.com"
                        />
                        <Button type="submit" variant="secondary" disabled={shareBusy || !shareEmail.trim()}>
                          {shareBusy ? <Loader2 className="h-4 w-4 animate-spin" /> : "Hinzufuegen"}
                        </Button>
                      </div>
                    </form>
                  )}
                </div>
              )}
            </DialogContent>
          </Dialog>
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
