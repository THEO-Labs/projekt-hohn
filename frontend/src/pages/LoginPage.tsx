import { useState } from "react";
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
      {/* Soft blue glow from top */}
      <div
        className="pointer-events-none absolute inset-0"
        style={{
          background:
            "radial-gradient(ellipse 70% 45% at 50% -5%, oklch(0.62 0.12 230 / 15%) 0%, transparent 65%)",
        }}
      />
      {/* Subtle grid pattern (dark lines on light bg) */}
      <div
        className="pointer-events-none absolute inset-0 opacity-[0.04]"
        style={{
          backgroundImage:
            "linear-gradient(oklch(0.2 0 0) 1px, transparent 1px), linear-gradient(90deg, oklch(0.2 0 0) 1px, transparent 1px)",
          backgroundSize: "40px 40px",
        }}
      />

      <form
        onSubmit={submit}
        className="relative z-10 w-full max-w-sm space-y-5 rounded-2xl border border-border bg-card p-8 shadow-xl shadow-black/5"
      >
        {/* Brand mark */}
        <div className="mb-8 flex flex-col items-center gap-3 text-center">
          <img src="/logo.svg" alt="Turning Point Investments" className="h-9 w-auto" />
          <p className="text-xs font-medium tracking-wide text-muted-foreground uppercase">
            {t.appTitle}
          </p>
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
