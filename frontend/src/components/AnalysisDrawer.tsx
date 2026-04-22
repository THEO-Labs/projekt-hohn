import { useEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { X, Sparkles, Send, RefreshCw, Search, FileSearch, Database, BrainCircuit } from "lucide-react";
import { toast } from "sonner";
import { t } from "@/lib/i18n";
import { formatValue } from "@/lib/format";
import { parseNumericInput } from "@/lib/parseNumeric";
import {
  analyzeValue,
  sendChatMessage,
  getChatHistory,
  type LlmMessage,
} from "@/api/llm";

type DataType = "NUMERIC" | "TEXT" | "FACTOR";

type AnalysisDrawerProps = {
  open: boolean;
  onClose: () => void;
  companyId: string;
  companyName: string;
  valueKey: string;
  valueLabel: string;
  currentScore: number | null;
  currentText?: string;
  dataType?: DataType;
  periodType?: string;
  periodYear?: number;
  onAcceptScore: (score: number | null, textValue?: string) => Promise<void>;
};

function formatTime(iso: string) {
  try {
    return new Date(iso).toLocaleTimeString("de-DE", { hour: "2-digit", minute: "2-digit" });
  } catch {
    return "";
  }
}

function toNum(v: unknown): number {
  if (typeof v === "number") return v;
  if (typeof v === "string") return parseFloat(v);
  return 0;
}

function escapeHtml(text: string): string {
  return text
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;");
}

function parseMarkdown(text: string): string {
  const escaped = escapeHtml(text);
  return escaped
    .replace(/\*\*(.+?)\*\*/g, "<strong>$1</strong>")
    .replace(/\[([^\]]+)\]\((https?:\/\/[^\s)]+)\)/g, '<a href="$2" target="_blank" rel="noreferrer" class="text-primary underline hover:opacity-80">$1</a>')
    .replace(/^(SCORE:|WERT:|EINHEIT:|QUELLE:|QUELLE_URL:|ZEITRAUM:|KONFIDENZ:|BEGRÜNDUNG:|FAKTOREN:|QUELLEN:|BEGRUENDUNG:)/gim, '<span class="font-semibold text-primary">$1</span>')
    .replace(/^- (.+)$/gm, '<li class="ml-4 list-disc">$1</li>')
    .replace(/(<li[^>]*>.*<\/li>\n?)+/g, (m) => `<ul class="space-y-0.5 my-1">${m}</ul>`)
    .replace(/\n/g, "<br />");
}

const THINKING_STAGES: { icon: typeof Search; text: string }[] = [
  { icon: BrainCircuit, text: "Analysiere Frage und Kontext…" },
  { icon: Search, text: "Durchsuche Investor-Relations-Seite…" },
  { icon: FileSearch, text: "Prüfe 10-K / SEC EDGAR Filings…" },
  { icon: Database, text: "Scanne Finanzdaten-Aggregatoren…" },
  { icon: FileSearch, text: "Verifiziere Zahlen gegen Quartalsberichte…" },
  { icon: BrainCircuit, text: "Fasse Ergebnis zusammen…" },
];

function ClaudeThinking() {
  const [stageIdx, setStageIdx] = useState(0);
  const [elapsed, setElapsed] = useState(0);
  useEffect(() => {
    const startedAt = Date.now();
    const tickElapsed = setInterval(() => {
      setElapsed(Math.floor((Date.now() - startedAt) / 1000));
    }, 1000);
    const tickStage = setInterval(() => {
      setStageIdx((i) => Math.min(i + 1, THINKING_STAGES.length - 1));
    }, 4500);
    return () => {
      clearInterval(tickElapsed);
      clearInterval(tickStage);
    };
  }, []);

  const stage = THINKING_STAGES[stageIdx];
  const StageIcon = stage.icon;

  return (
    <div className="flex justify-start">
      <div className="max-w-[90%] rounded-2xl rounded-bl-sm border border-primary/20 bg-primary/5 px-4 py-3 shadow-sm">
        <div className="flex items-center gap-2.5">
          <div className="relative flex h-8 w-8 shrink-0 items-center justify-center rounded-full bg-primary/15">
            <StageIcon className="h-4 w-4 text-primary" />
            <span className="absolute -right-0.5 -top-0.5 flex h-2.5 w-2.5">
              <span className="absolute inline-flex h-full w-full animate-ping rounded-full bg-primary/50" />
              <span className="relative inline-flex h-2.5 w-2.5 rounded-full bg-primary" />
            </span>
          </div>
          <div className="flex flex-col gap-0.5">
            <div className="flex items-center gap-1.5">
              <span className="text-sm font-medium text-foreground">{stage.text}</span>
              <span className="flex gap-0.5">
                <span className="h-1 w-1 animate-bounce rounded-full bg-primary [animation-delay:0ms]" />
                <span className="h-1 w-1 animate-bounce rounded-full bg-primary [animation-delay:150ms]" />
                <span className="h-1 w-1 animate-bounce rounded-full bg-primary [animation-delay:300ms]" />
              </span>
            </div>
            <span className="text-[11px] text-muted-foreground">
              {elapsed < 10 ? "Claude recherchiert…" : `Claude recherchiert seit ${elapsed}s (kann bis zu 60s dauern)`}
            </span>
          </div>
        </div>
      </div>
    </div>
  );
}

export function AnalysisDrawer({
  open,
  onClose,
  companyId,
  companyName,
  valueKey,
  valueLabel,
  currentScore,
  currentText,
  dataType = "NUMERIC",
  periodType,
  periodYear,
  onAcceptScore,
}: AnalysisDrawerProps) {
  const isFactorType = dataType === "FACTOR";
  const isTextType = dataType === "TEXT";

  const [messages, setMessages] = useState<LlmMessage[]>([]);
  const [sliderValue, setSliderValue] = useState<number>(currentScore ?? 1.0);
  const [numericInput, setNumericInput] = useState<string>(
    currentScore != null ? String(currentScore) : ""
  );
  const [textValue, setTextValue] = useState<string>("");
  const [inputText, setInputText] = useState("");
  const [analyzing, setAnalyzing] = useState(false);
  const [sending, setSending] = useState(false);
  const [accepting, setAccepting] = useState(false);
  const [historyLoaded, setHistoryLoaded] = useState(false);
  const messagesEndRef = useRef<HTMLDivElement>(null);

  const applySliderValue = (v: number) => {
    setSliderValue(v);
    setNumericInput(String(v));
  };

  useEffect(() => {
    setMessages([]);
    setInputText("");
    setHistoryLoaded(false);
    applySliderValue(currentScore ?? 1.0);
    setTextValue(currentText ?? "");
  }, [companyId, valueKey, periodType, periodYear, currentScore, currentText]);

  useEffect(() => {
    if (!open) return;
    getChatHistory(companyId, valueKey, periodType, periodYear)
      .then((res) => {
        setMessages(res.messages);
        const lastSuggestion = [...res.messages]
          .reverse()
          .find((m) => m.score_suggestion != null);
        if (lastSuggestion?.score_suggestion != null) {
          applySliderValue(toNum(lastSuggestion.score_suggestion));
        } else if (currentScore != null) {
          applySliderValue(currentScore);
        }
      })
      .catch(() => setMessages([]))
      .finally(() => setHistoryLoaded(true));
  }, [open, companyId, valueKey, periodType, periodYear, currentScore]);

  useEffect(() => {
    if (open) {
      messagesEndRef.current?.scrollIntoView({ behavior: "smooth" });
    }
  }, [messages, open]);

  const handleAnalyze = async (force = false) => {
    setAnalyzing(true);
    try {
      const res = await analyzeValue(companyId, valueKey, periodType, periodYear, force);
      setMessages((prev) => {
        const exists = prev.some((m) => m.id === res.message.id);
        return exists ? prev : [...prev, res.message];
      });
      if (res.message.score_suggestion != null) {
        applySliderValue(toNum(res.message.score_suggestion));
      }
    } catch {
      toast.error("Analyse fehlgeschlagen. Bitte erneut versuchen.");
    } finally {
      setAnalyzing(false);
    }
  };

  const handleSend = async () => {
    const text = inputText.trim();
    if (!text || sending) return;
    setInputText("");
    setSending(true);
    const optimisticId = `tmp-${Date.now()}-${Math.random().toString(36).slice(2)}`;
    const optimisticUserMsg: LlmMessage = {
      id: optimisticId,
      role: "user",
      content: text,
      score_suggestion: null,
      created_at: new Date().toISOString(),
    };
    setMessages((prev) => [...prev, optimisticUserMsg]);
    try {
      const res = await sendChatMessage(companyId, valueKey, text, periodType, periodYear);
      setMessages((prev) => [...prev, res.message]);
      if (res.message.score_suggestion != null) {
        applySliderValue(toNum(res.message.score_suggestion));
      }
    } catch {
      toast.error("Nachricht konnte nicht gesendet werden.");
      setMessages((prev) => prev.filter((m) => m.id !== optimisticId));
    } finally {
      setSending(false);
    }
  };

  const handleKeyDown = (e: React.KeyboardEvent<HTMLTextAreaElement>) => {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      handleSend();
    }
  };

  const handleAccept = async () => {
    if (accepting) return;
    setAccepting(true);
    try {
      if (isTextType) {
        await onAcceptScore(null, textValue);
      } else {
        await onAcceptScore(sliderValue);
      }
    } catch {
      toast.error("Wert konnte nicht gespeichert werden.");
    } finally {
      setAccepting(false);
    }
  };

  const hasMessages = messages.length > 0;

  const acceptLabel = isTextType
    ? "Einschätzung übernehmen"
    : isFactorType
    ? `${t.acceptScore} (${toNum(sliderValue).toFixed(2)})`
    : `Wert übernehmen (${formatValue(sliderValue, null, null)})`;

  return createPortal(
    <div className={`fixed inset-0 z-[200] flex justify-end transition-opacity duration-200 ${open ? "opacity-100 pointer-events-auto" : "opacity-0 pointer-events-none"}`}>
      <div
        className="absolute inset-0 bg-black/30"
        onClick={onClose}
      />
      <div
        className={`relative flex h-full w-full flex-col bg-card shadow-2xl transition-transform duration-300 sm:w-[480px] ${
          open ? "translate-x-0" : "translate-x-full"
        }`}
      >
        <header className="flex shrink-0 items-start justify-between border-b border-border px-5 py-4">
          <div>
            <div className="flex items-center gap-2">
              <Sparkles className="h-4 w-4 text-primary" />
              <span className="text-sm font-semibold text-foreground">{valueLabel}</span>
            </div>
            <p className="mt-0.5 text-xs text-muted-foreground">{companyName}</p>
          </div>
          <button
            onClick={onClose}
            className="rounded-md p-1.5 text-muted-foreground transition-colors hover:bg-muted hover:text-foreground"
          >
            <X className="h-4 w-4" />
          </button>
        </header>

        <div className="shrink-0 border-b border-border px-5 py-3">
          <div className="flex items-center justify-between">
            <span className="text-xs font-medium text-muted-foreground">
              {isFactorType ? t.scoreLabel : isTextType ? "Einschätzung" : "Wert"}
            </span>
            {isFactorType ? (
              <span className="font-mono text-sm font-semibold text-primary">{toNum(sliderValue).toFixed(2)}</span>
            ) : isTextType ? null : (
              <input
                type="text"
                className="w-40 rounded border border-input bg-background px-2 py-1 text-right font-mono text-sm text-foreground outline-none focus:ring-1 focus:ring-primary"
                value={numericInput}
                onChange={(e) => {
                  const raw = e.target.value;
                  setNumericInput(raw);
                  if (raw === "") {
                    setSliderValue(0);
                    return;
                  }
                  const v = parseNumericInput(raw);
                  if (!isNaN(v)) setSliderValue(v);
                }}
              />
            )}
          </div>
          {isFactorType && (
            <>
              <input
                type="range"
                min={0.5}
                max={1.5}
                step={0.05}
                value={sliderValue}
                onChange={(e) => applySliderValue(Number(e.target.value))}
                className="mt-2 w-full accent-primary"
              />
              <div className="flex justify-between text-[10px] text-muted-foreground">
                <span>0.50</span>
                <span>1.00</span>
                <span>1.50</span>
              </div>
            </>
          )}
          {isTextType && (
            <textarea
              rows={3}
              placeholder="Einschätzung eingeben..."
              value={textValue}
              onChange={(e) => setTextValue(e.target.value)}
              className="mt-2 w-full resize-none rounded border border-input bg-background px-2 py-1.5 text-sm text-foreground placeholder:text-muted-foreground focus:outline-none focus:ring-1 focus:ring-primary"
            />
          )}
        </div>

        {hasMessages && (
          <div className="shrink-0 border-b border-border px-5 py-2">
            <button
              onClick={() => handleAnalyze(true)}
              disabled={analyzing}
              className="flex items-center gap-1.5 rounded-lg border border-border px-3 py-1.5 text-xs font-medium text-muted-foreground transition-colors hover:bg-muted hover:text-foreground disabled:opacity-50"
            >
              <RefreshCw className={`h-3 w-3 ${analyzing ? "animate-spin" : ""}`} />
              Neue Analyse starten
            </button>
          </div>
        )}

        <div className="flex-1 overflow-y-auto px-4 py-4">
          {historyLoaded && messages.length === 0 ? (
            <div className="flex h-full flex-col items-center justify-center gap-4 text-center">
              <div className="rounded-full bg-primary/10 p-4">
                <Sparkles className="h-8 w-8 text-primary" />
              </div>
              <div>
                <p className="text-sm font-medium text-foreground">Noch keine Analyse vorhanden</p>
                <p className="mt-1 text-xs text-muted-foreground">
                  Starte eine Claude-Analyse um eine Einschätzung zu erhalten.
                </p>
              </div>
              <button
                onClick={() => handleAnalyze(false)}
                disabled={analyzing}
                className="flex items-center gap-2 rounded-lg bg-primary px-4 py-2.5 text-sm font-medium text-primary-foreground transition-opacity hover:opacity-90 disabled:opacity-60"
              >
                {analyzing ? (
                  <>
                    <svg className="h-4 w-4 animate-spin" viewBox="0 0 24 24" fill="none">
                      <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4" />
                      <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8v8H4z" />
                    </svg>
                    {t.analyzing}
                  </>
                ) : (
                  <>
                    <Sparkles className="h-4 w-4" />
                    {t.analyzeStart}
                  </>
                )}
              </button>
            </div>
          ) : (
            <div className="space-y-3">
              {messages.map((msg) => {
                if (msg.role === "system") {
                  return (
                    <div key={msg.id} className="flex justify-center">
                      <span className="italic text-[11px] text-muted-foreground/70 px-3 py-1 rounded-full bg-muted/50">
                        {msg.content}
                      </span>
                    </div>
                  );
                }
                return (
                  <div
                    key={msg.id}
                    className={`flex ${msg.role === "user" ? "justify-end" : "justify-start"}`}
                  >
                    <div
                      className={`max-w-[85%] rounded-2xl px-4 py-2.5 text-sm ${
                        msg.role === "user"
                          ? "rounded-br-sm bg-primary text-primary-foreground"
                          : "rounded-bl-sm bg-muted text-foreground"
                      }`}
                    >
                      {msg.role === "assistant" ? (
                        <div
                          className="prose-xs leading-relaxed"
                          dangerouslySetInnerHTML={{ __html: parseMarkdown(msg.content) }}
                        />
                      ) : (
                        <p className="leading-relaxed">{msg.content}</p>
                      )}
                      <div
                        className={`mt-1.5 flex items-center gap-2 ${
                          msg.role === "user" ? "justify-end" : "justify-start"
                        }`}
                      >
                        <span
                          className={`text-[10px] ${
                            msg.role === "user" ? "text-primary-foreground/70" : "text-muted-foreground"
                          }`}
                        >
                          {formatTime(msg.created_at)}
                        </span>
                        {msg.score_suggestion != null && (
                          <span className="rounded bg-primary/15 px-1.5 py-0.5 font-mono text-[10px] font-semibold text-primary">
                            {isFactorType
                              ? `Score: ${toNum(msg.score_suggestion).toFixed(2)}`
                              : `Wert: ${formatValue(toNum(msg.score_suggestion), null, null)}`}
                          </span>
                        )}
                      </div>
                    </div>
                  </div>
                );
              })}
              {(analyzing || sending) && <ClaudeThinking />}
              <div ref={messagesEndRef} />
            </div>
          )}
        </div>

        <div className="shrink-0 border-t border-border px-4 py-3">
          <div className="flex items-end gap-2">
            <textarea
              value={inputText}
              onChange={(e) => setInputText(e.target.value)}
              onKeyDown={handleKeyDown}
              placeholder={t.chatPlaceholder}
              rows={2}
              disabled={sending || analyzing}
              className="flex-1 resize-none rounded-xl border border-input bg-background px-3 py-2 text-sm text-foreground placeholder:text-muted-foreground focus:outline-none focus:ring-2 focus:ring-ring disabled:opacity-50"
            />
            <button
              onClick={handleSend}
              disabled={!inputText.trim() || sending || analyzing}
              className="flex h-9 w-9 shrink-0 items-center justify-center rounded-xl bg-primary text-primary-foreground transition-opacity hover:opacity-90 disabled:opacity-40"
            >
              {sending ? (
                <svg className="h-4 w-4 animate-spin" viewBox="0 0 24 24" fill="none">
                  <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4" />
                  <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8v8H4z" />
                </svg>
              ) : (
                <Send className="h-4 w-4" />
              )}
            </button>
          </div>
        </div>

        <footer className="shrink-0 border-t border-border px-4 py-3 space-y-2">
          <button
            onClick={handleAccept}
            disabled={accepting}
            className="w-full rounded-xl bg-primary py-2.5 text-sm font-medium text-primary-foreground transition-opacity hover:opacity-90 disabled:opacity-60"
          >
            {accepting ? "..." : acceptLabel}
          </button>
          {historyLoaded && messages.filter((m) => m.role !== "system").length === 0 && !analyzing && (
            <button
              onClick={() => handleAnalyze(false)}
              disabled={analyzing}
              className="flex w-full items-center justify-center gap-2 rounded-xl border border-border py-2.5 text-sm font-medium text-muted-foreground transition-colors hover:bg-muted hover:text-foreground disabled:opacity-60"
            >
              <Sparkles className="h-4 w-4" />
              {t.analyzeStart}
            </button>
          )}
        </footer>
      </div>
    </div>,
    document.body
  );
}
