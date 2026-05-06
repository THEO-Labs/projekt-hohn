import { useEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { X, Sparkles, Send, Search, FileSearch, Database, BrainCircuit, HelpCircle, TrendingUp, ShieldCheck, Calculator, Scale } from "lucide-react";
import { toast } from "sonner";
import { t } from "@/lib/i18n";
import { formatValue } from "@/lib/format";
import { parseNumericInput } from "@/lib/parseNumeric";
import {
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
  isCalculated?: boolean;
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

type QuickPrompt = {
  icon: typeof Search;
  label: string;
  prompt: string;
  enableSearch: boolean;
  hint?: string;
};

// Quick-Prompts wenn der Wert vorhanden ist — Validierung + Erklaerung.
const HAS_VALUE_PROMPTS: QuickPrompt[] = [
  {
    icon: ShieldCheck,
    label: "Plausibilitäts-Check",
    prompt: "Pruefe die Plausibilitaet dieses Werts gegen den Annual Report, historische Werte derselben Firma und ggf. Branchen-Vergleichswerte. Recherchiere aktiv nach One-Time-Items, Restructuring-Charges, Akquisitions-Effekten, Yeezy/Special-Cases o.ae. die diesen Wert verzerren koennten. Nutze web_search auf IR-Seite, Aggregatoren (stockanalysis.com, macrotrends.net) und Earnings-Call-Transcripts.",
    enableSearch: true,
    hint: "ca. 30s, mit Web-Recherche",
  },
  {
    icon: HelpCircle,
    label: "Was bedeutet dieser Wert?",
    prompt: "Erklaere kurz und klar was diese Kennzahl in diesem Geschaeftsjahr fuer einen langfristigen Aktionaer bedeutet. Wie ordnet sich der Wert ein (gut/normal/schwach), und was sind die operativen Implikationen? 3-5 Saetze, keine Web-Recherche noetig.",
    enableSearch: false,
  },
  {
    icon: TrendingUp,
    label: "Vergleich zum Vorjahr",
    prompt: "Vergleiche diesen Wert mit dem Vorjahr (siehe Kontext). Berechne die absolute und prozentuale Veraenderung, identifiziere die 1-2 wichtigsten Treiber des Unterschieds. Falls operative Hintergruende noetig sind, kurz Industrie-Kontext einbauen.",
    enableSearch: false,
  },
];

// Quick-Prompts wenn der Wert FEHLT — Recherche + Approximation.
const NO_VALUE_PROMPTS: QuickPrompt[] = [
  {
    icon: Search,
    label: "Mit Claude im Web recherchieren",
    prompt: "Der Wert konnte nicht aus dem Annual Report extrahiert werden. Recherchiere aktiv via web_search bei Daten-Aggregatoren (stockanalysis.com, macrotrends.net, wsj.com, wisesheets.io, simplywall.st) und liefere den Wert im strikten WERT/EINHEIT/QUELLE/QUELLE_URL/ZEITRAUM/KONFIDENZ/BEGRUENDUNG-Format zurueck. Nutze nur verifizierbare Aggregator-Daten, keine Schaetzungen.",
    enableSearch: true,
    hint: "ca. 30s, mit Web-Recherche",
  },
  {
    icon: Calculator,
    label: "Aus verfügbaren Werten approximieren",
    prompt: "Der Wert fehlt. Nutze die anderen verfuegbaren Werte derselben Firma im Kontext (Bilanz, GuV, Cashflow), um einen plausiblen Approximations-Wert herzuleiten — z.B. FCF = Operating Cash Flow − CapEx, Total Debt aus Komponenten, etc. Erklaere die Herleitung Schritt fuer Schritt und gib die KONFIDENZ klar an. Keine Web-Recherche.",
    enableSearch: false,
  },
  {
    icon: FileSearch,
    label: "Im Bericht nochmal gezielt suchen",
    prompt: "Der Wert wurde im PDF nicht gefunden. Geh nochmal SYSTEMATISCH durch alle typischen Stellen eines Annual Reports: Cash Flow Statement, Statement of Changes in Equity, Notes (Personnel, Other liabilities, Equity, Share-based payments). Bei IFRS-Filern oft NICHT im CF-Statement! Liste exakt welche Seiten du geprueft hast und was du gefunden / nicht gefunden hast.",
    enableSearch: false,
  },
];

type ThinkingMode = "research" | "analysis-quick" | "analysis-deep" | "qualitative";

const STAGES_BY_MODE: Record<ThinkingMode, { icon: typeof Search; text: string }[]> = {
  research: [
    { icon: BrainCircuit, text: "Analysiere Frage und Kontext…" },
    { icon: Search, text: "Durchsuche Investor-Relations-Seite…" },
    { icon: FileSearch, text: "Prüfe 10-K / SEC EDGAR Filings…" },
    { icon: Database, text: "Scanne Finanzdaten-Aggregatoren…" },
    { icon: FileSearch, text: "Verifiziere Zahlen gegen Quartalsberichte…" },
    { icon: BrainCircuit, text: "Fasse Ergebnis zusammen…" },
  ],
  "analysis-quick": [
    { icon: BrainCircuit, text: "Lese Komponenten aus dem Kontext…" },
    { icon: Calculator, text: "Berechne Beiträge der einzelnen Komponenten…" },
    { icon: TrendingUp, text: "Identifiziere Treiber und Effekte…" },
    { icon: BrainCircuit, text: "Formuliere Analyse…" },
  ],
  "analysis-deep": [
    { icon: BrainCircuit, text: "Lese Komponenten aus dem Kontext…" },
    { icon: Calculator, text: "Berechne Beiträge…" },
    { icon: Search, text: "Suche nach One-Time-Items und Sondereffekten…" },
    { icon: FileSearch, text: "Prüfe Earnings-Call-Transcripts…" },
    { icon: ShieldCheck, text: "Bewerte Plausibilität…" },
    { icon: BrainCircuit, text: "Fasse Analyse zusammen…" },
  ],
  qualitative: [
    { icon: BrainCircuit, text: "Analysiere die Bewertungskriterien…" },
    { icon: Search, text: "Sammle Informationen zur Firma…" },
    { icon: Scale, text: "Wäge positive und negative Faktoren ab…" },
    { icon: BrainCircuit, text: "Formuliere Score und Begründung…" },
  ],
};

const MODE_HINT_TEXT: Record<ThinkingMode, (sec: number) => string> = {
  research: (sec) => sec < 10 ? "Claude recherchiert…" : `Claude recherchiert seit ${sec}s (kann bis zu 60s dauern)`,
  "analysis-quick": (sec) => sec < 8 ? "Claude analysiert…" : `Claude analysiert seit ${sec}s`,
  "analysis-deep": (sec) => sec < 10 ? "Claude prüft Plausibilität…" : `Claude recherchiert seit ${sec}s (kann bis zu 60s dauern)`,
  qualitative: (sec) => sec < 8 ? "Claude bewertet…" : `Claude bewertet seit ${sec}s`,
};

function ClaudeThinking({ mode }: { mode: ThinkingMode }) {
  const stages = STAGES_BY_MODE[mode];
  const [stageIdx, setStageIdx] = useState(0);
  const [elapsed, setElapsed] = useState(0);
  useEffect(() => {
    const startedAt = Date.now();
    const tickElapsed = setInterval(() => {
      setElapsed(Math.floor((Date.now() - startedAt) / 1000));
    }, 1000);
    const tickStage = setInterval(() => {
      setStageIdx((i) => Math.min(i + 1, stages.length - 1));
    }, 4500);
    return () => {
      clearInterval(tickElapsed);
      clearInterval(tickStage);
    };
  }, [stages.length]);

  const stage = stages[stageIdx];
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
              {MODE_HINT_TEXT[mode](elapsed)}
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
  isCalculated = false,
  periodType,
  periodYear,
  onAcceptScore,
}: AnalysisDrawerProps) {
  const isFactorType = dataType === "FACTOR";
  const isTextType = dataType === "TEXT";
  const hasValue = isTextType
    ? Boolean(currentText && currentText.trim())
    : currentScore != null;

  const [messages, setMessages] = useState<LlmMessage[]>([]);
  const [sliderValue, setSliderValue] = useState<number>(currentScore ?? 1.0);
  const [numericInput, setNumericInput] = useState<string>(
    currentScore != null ? String(currentScore) : ""
  );
  const [textValue, setTextValue] = useState<string>("");
  const [inputText, setInputText] = useState("");
  const analyzing = false;
  const [sending, setSending] = useState(false);
  const [accepting, setAccepting] = useState(false);
  const [historyLoaded, setHistoryLoaded] = useState(false);
  const defaultThinkingMode: ThinkingMode = isFactorType
    ? "qualitative"
    : isCalculated
    ? "analysis-quick"
    : "research";
  const [thinkingMode, setThinkingMode] = useState<ThinkingMode>(defaultThinkingMode);
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

  const handleSend = async (overrideText?: string, enableSearch = false) => {
    const text = (overrideText ?? inputText).trim();
    if (!text || sending) return;
    if (overrideText === undefined) setInputText("");
    setSending(true);
    setThinkingMode(
      isFactorType
        ? "qualitative"
        : isCalculated
        ? (enableSearch ? "analysis-deep" : "analysis-quick")
        : "research"
    );
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
      const res = await sendChatMessage(companyId, valueKey, text, periodType, periodYear, enableSearch);
      setMessages((prev) => [...prev, res.message]);
      if (res.message.score_suggestion != null) {
        applySliderValue(toNum(res.message.score_suggestion));
      }
    } catch (e) {
      const detail = (e as { message?: string })?.message;
      toast.error(detail || "Nachricht konnte nicht gesendet werden.");
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
                readOnly={isCalculated}
                className={`w-40 rounded border border-input bg-background px-2 py-1 text-right font-mono text-sm text-foreground outline-none focus:ring-1 focus:ring-primary ${isCalculated ? "cursor-not-allowed opacity-70" : ""}`}
                value={numericInput}
                onChange={(e) => {
                  if (isCalculated) return;
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

        {(() => {
          const promptSet = hasValue ? HAS_VALUE_PROMPTS : NO_VALUE_PROMPTS;
          const headline = hasValue
            ? "Wert vorhanden — Claude kann erklären / validieren"
            : "Wert fehlt — Claude kann recherchieren / approximieren";
          return (
            <div className="shrink-0 border-b border-border bg-muted/20 px-4 py-3">
              <p className="mb-2 text-[10px] font-semibold uppercase tracking-wide text-muted-foreground">
                {headline}
              </p>
              <div className="grid grid-cols-1 gap-1.5">
                {promptSet.map((q) => {
                  const Icon = q.icon;
                  return (
                    <button
                      key={q.label}
                      onClick={() => handleSend(q.prompt, q.enableSearch)}
                      disabled={sending || analyzing}
                      className="flex items-start gap-2.5 rounded-md border border-border bg-background px-2.5 py-1.5 text-left text-xs text-foreground transition-colors hover:border-primary/50 hover:bg-primary/5 disabled:opacity-50"
                    >
                      <Icon className="mt-0.5 h-3.5 w-3.5 shrink-0 text-primary" />
                      <div className="flex flex-col">
                        <span className="font-medium">{q.label}</span>
                        {q.hint && (
                          <span className="text-[10px] text-muted-foreground">{q.hint}</span>
                        )}
                      </div>
                    </button>
                  );
                })}
              </div>
            </div>
          );
        })()}

        <div className="flex-1 overflow-y-auto px-4 py-4">
          {historyLoaded && messages.length === 0 ? (
            <div className="flex h-full flex-col items-center justify-center gap-3 px-4 text-center">
              <div className="rounded-full bg-muted p-3">
                <Sparkles className="h-6 w-6 text-muted-foreground" />
              </div>
              <p className="text-xs text-muted-foreground max-w-[280px]">
                Noch keine Konversation. Wähle einen Quick-Prompt oben oder stelle eine eigene Frage unten.
              </p>
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
              {(analyzing || sending) && <ClaudeThinking mode={thinkingMode} />}
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
              onClick={() => handleSend()}
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
          {isCalculated ? (
            <p className="rounded-xl border border-border bg-muted/40 px-3 py-2.5 text-center text-xs text-muted-foreground">
              Berechneter Wert (Formel) - nicht direkt editierbar. Korrigiere die Eingangswerte um diesen Wert zu beeinflussen.
            </p>
          ) : (
            <button
              onClick={handleAccept}
              disabled={accepting}
              className="w-full rounded-xl bg-primary py-2.5 text-sm font-medium text-primary-foreground transition-opacity hover:opacity-90 disabled:opacity-60"
            >
              {accepting ? "..." : acceptLabel}
            </button>
          )}
        </footer>
      </div>
    </div>,
    document.body
  );
}
