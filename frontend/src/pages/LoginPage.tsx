import { useState } from "react";
import { TrendingUp } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { t } from "@/lib/i18n";

type Props = { onLogin: (email: string, password: string) => Promise<void> };

export function LoginPage({ onLogin }: Props) {
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);

  const submit = async (e: React.FormEvent) => {
    e.preventDefault();
    setError(null);
    setSubmitting(true);
    try {
      await onLogin(email, password);
    } catch {
      setError(t.loginFailed);
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <div className="relative flex min-h-screen items-center justify-center overflow-hidden bg-background">
      {/* Radial gradient glow */}
      <div
        className="pointer-events-none absolute inset-0"
        style={{
          background:
            "radial-gradient(ellipse 80% 50% at 50% -10%, oklch(0.6 0.15 155.56 / 18%) 0%, transparent 70%)",
        }}
      />
      {/* Subtle grid pattern */}
      <div
        className="pointer-events-none absolute inset-0 opacity-[0.025]"
        style={{
          backgroundImage:
            "linear-gradient(oklch(1 0 0) 1px, transparent 1px), linear-gradient(90deg, oklch(1 0 0) 1px, transparent 1px)",
          backgroundSize: "40px 40px",
        }}
      />

      <form
        onSubmit={submit}
        className="relative z-10 w-full max-w-sm space-y-5 rounded-xl border border-border/60 bg-card/90 p-8 shadow-2xl shadow-black/40 backdrop-blur-sm"
      >
        {/* Brand mark */}
        <div className="mb-6 flex flex-col items-center gap-3 text-center">
          <div className="flex h-11 w-11 items-center justify-center rounded-xl bg-primary/15 ring-1 ring-primary/25">
            <TrendingUp className="h-6 w-6 text-primary" strokeWidth={2} />
          </div>
          <div>
            <h1 className="text-xl font-semibold tracking-tight text-foreground">{t.appTitle}</h1>
            <p className="mt-0.5 text-xs text-muted-foreground">Turning Point Investments</p>
          </div>
        </div>

        <div className="space-y-1.5">
          <Label htmlFor="email" className="text-sm text-muted-foreground">
            {t.email}
          </Label>
          <Input
            id="email"
            type="email"
            value={email}
            onChange={(e) => setEmail(e.target.value)}
            placeholder="name@example.com"
            className="bg-background/50 transition-colors focus:bg-background"
            required
          />
        </div>

        <div className="space-y-1.5">
          <Label htmlFor="password" className="text-sm text-muted-foreground">
            {t.password}
          </Label>
          <Input
            id="password"
            type="password"
            value={password}
            onChange={(e) => setPassword(e.target.value)}
            placeholder="••••••••"
            className="bg-background/50 transition-colors focus:bg-background"
            required
          />
        </div>

        {error && (
          <div className="rounded-lg border border-destructive/30 bg-destructive/10 px-3 py-2.5">
            <p className="text-sm text-destructive">{error}</p>
          </div>
        )}

        <Button
          type="submit"
          disabled={submitting}
          className="w-full bg-primary font-medium text-primary-foreground shadow-lg shadow-primary/20 transition-all hover:bg-primary/90 hover:shadow-primary/30"
        >
          {submitting ? (
            <span className="flex items-center gap-2">
              <span className="h-3.5 w-3.5 animate-spin rounded-full border-2 border-current border-t-transparent" />
              Anmelden...
            </span>
          ) : (
            t.login
          )}
        </Button>
      </form>
    </div>
  );
}
