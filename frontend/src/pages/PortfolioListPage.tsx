import { useEffect, useState } from "react";
import { Link } from "react-router-dom";

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

  const refresh = () => listPortfolios().then(setPortfolios);

  useEffect(() => {
    refresh();
  }, []);

  const submit = async (e: React.FormEvent) => {
    e.preventDefault();
    await createPortfolio(name);
    setName("");
    setOpen(false);
    refresh();
  };

  const remove = async (id: string) => {
    await deletePortfolio(id);
    refresh();
  };

  if (!user) return null;

  return (
    <div className="min-h-screen bg-slate-50">
      <AppHeader email={user.email} onLogout={logout} />
      <main className="mx-auto max-w-4xl p-6">
        <div className="mb-6 flex items-center justify-between">
          <h2 className="text-2xl font-semibold">{t.portfolios}</h2>
          <Dialog open={open} onOpenChange={setOpen}>
            <DialogTrigger render={<Button />}>
              {t.newPortfolio}
            </DialogTrigger>
            <DialogContent>
              <DialogHeader>
                <DialogTitle>{t.newPortfolio}</DialogTitle>
              </DialogHeader>
              <form onSubmit={submit} className="space-y-4">
                <div className="space-y-2">
                  <Label htmlFor="pname">{t.portfolioName}</Label>
                  <Input id="pname" value={name} onChange={(e) => setName(e.target.value)} required />
                </div>
                <div className="flex justify-end gap-2">
                  <Button type="button" variant="ghost" onClick={() => setOpen(false)}>
                    {t.cancel}
                  </Button>
                  <Button type="submit">{t.save}</Button>
                </div>
              </form>
            </DialogContent>
          </Dialog>
        </div>

        <ul className="divide-y rounded-lg border bg-white">
          {portfolios.map((p) => (
            <li key={p.id} className="flex items-center justify-between px-4 py-3">
              <Link to={`/portfolios/${p.id}`} className="font-medium hover:underline">
                {p.name}
              </Link>
              <Button variant="ghost" size="sm" onClick={() => remove(p.id)}>
                {t.delete}
              </Button>
            </li>
          ))}
          {portfolios.length === 0 && (
            <li className="px-4 py-8 text-center text-sm text-slate-500">Noch keine Portfolios</li>
          )}
        </ul>
      </main>
    </div>
  );
}
