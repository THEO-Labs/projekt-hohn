import { useEffect, useState } from "react";
import { Link, useParams } from "react-router-dom";

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
  type Company,
} from "@/api/companies";
import { t } from "@/lib/i18n";

export function PortfolioDetailPage() {
  const { id } = useParams<{ id: string }>();
  const { user, logout } = useAuth();
  const [companies, setCompanies] = useState<Company[]>([]);
  const [open, setOpen] = useState(false);
  const [form, setForm] = useState({ name: "", ticker: "", isin: "", currency: "EUR" });

  const refresh = () => {
    if (id) listCompanies(id).then(setCompanies);
  };

  useEffect(() => {
    refresh();
  }, [id]);

  const submit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!id) return;
    await createCompany(id, {
      name: form.name,
      ticker: form.ticker,
      currency: form.currency,
      isin: form.isin || undefined,
    });
    setForm({ name: "", ticker: "", isin: "", currency: "EUR" });
    setOpen(false);
    refresh();
  };

  const remove = async (cid: string) => {
    await deleteCompany(cid);
    refresh();
  };

  if (!user) return null;

  return (
    <div className="min-h-screen bg-slate-50">
      <AppHeader email={user.email} onLogout={logout} />
      <main className="mx-auto max-w-4xl p-6">
        <div className="mb-4">
          <Link to="/" className="text-sm text-slate-600 hover:underline">{t.back}</Link>
        </div>
        <div className="mb-6 flex items-center justify-between">
          <h2 className="text-2xl font-semibold">{t.companies}</h2>
          <Dialog open={open} onOpenChange={setOpen}>
            <DialogTrigger render={<Button />}>{t.newCompany}</DialogTrigger>
            <DialogContent>
              <DialogHeader>
                <DialogTitle>{t.newCompany}</DialogTitle>
              </DialogHeader>
              <form onSubmit={submit} className="space-y-4">
                <Field label={t.companyName} value={form.name}
                       onChange={(v) => setForm({ ...form, name: v })} required />
                <Field label={t.ticker} value={form.ticker}
                       onChange={(v) => setForm({ ...form, ticker: v })} required />
                <Field label={t.isin} value={form.isin}
                       onChange={(v) => setForm({ ...form, isin: v })} />
                <Field label={t.currency} value={form.currency}
                       onChange={(v) => setForm({ ...form, currency: v.toUpperCase() })} required />
                <div className="flex justify-end gap-2">
                  <Button type="button" variant="ghost" onClick={() => setOpen(false)}>{t.cancel}</Button>
                  <Button type="submit">{t.save}</Button>
                </div>
              </form>
            </DialogContent>
          </Dialog>
        </div>

        <ul className="divide-y rounded-lg border bg-white">
          {companies.map((c) => (
            <li key={c.id} className="flex items-center justify-between px-4 py-3">
              <div>
                <div className="font-medium">{c.name}</div>
                <div className="text-sm text-slate-600">{c.ticker} - {c.currency}</div>
              </div>
              <Button variant="ghost" size="sm" onClick={() => remove(c.id)}>{t.delete}</Button>
            </li>
          ))}
          {companies.length === 0 && (
            <li className="px-4 py-8 text-center text-sm text-slate-500">Noch keine Firmen</li>
          )}
        </ul>
      </main>
    </div>
  );
}

function Field({
  label, value, onChange, required,
}: { label: string; value: string; onChange: (v: string) => void; required?: boolean }) {
  return (
    <div className="space-y-2">
      <Label>{label}</Label>
      <Input value={value} onChange={(e) => onChange(e.target.value)} required={required} />
    </div>
  );
}
