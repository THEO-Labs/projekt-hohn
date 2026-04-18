import { useEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { X, Sparkles, Send } from "lucide-react";
import { t } from "@/lib/i18n";
import {
  analyzeValue,
  sendChatMessage,
  getChatHistory,
  type LlmMessage,
} from "@/api/llm";

type AnalysisDrawerProps = {
  open: boolean;
  onClose: () => void;
  companyId: string;
  companyName: string;
  valueKey: string;
  valueLabel: string;
  currentScore: number | null;
  isQualitative?: boolean;
  onAcceptScore: (score: number) => void;
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
    .replace(/^(SCORE:|BEGRÜNDUNG:|FAKTOREN:|QUELLEN:|BEGRUENDUNG:)/gim, '<span class="font-semibold text-primary">$1</span>')
    .replace(/^- (.+)$/gm, '<li class="ml-4 list-disc">$1</li>')
    .replace(/(<li[^>]*>.*<\/li>\n?)+/g, (m) => `<ul class="space-y-0.5 my-1">${m}</ul>`)
    .replace(/\n/g, "<br />");
}

export function AnalysisDrawer({
  open,
  onClose,
  companyId,
  companyName,
  valueKey,
  valueLabel,
  currentScore,
  isQualitative = false,
  onAcceptScore,
}: AnalysisDrawerProps) {
  const [messages, setMessages] = useState<LlmMessage[]>([]);
  const [sliderValue, setSliderValue] = useState<number>(typeof currentScore === "string" ? parseFloat(currentScore) : currentScore ?? 1.0);
  const [inputText, setInputText] = useState("");
  const [analyzing, setAnalyzing] = useState(false);
  const [sending, setSending] = useState(false);
  const [accepting, setAccepting] = useState(false);
  const [historyLoaded, setHistoryLoaded] = useState(false);
  const messagesEndRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!open) return;
    setHistoryLoaded(false);
    getChatHistory(companyId, valueKey)
      .then((res) => {
        setMessages(res.messages);
        const lastSuggestion = [...res.messages]
          .reverse()
          .find((m) => m.score_suggestion != null);
        if (lastSuggestion?.score_suggestion != null) {
          setSliderValue(toNum(lastSuggestion.score_suggestion));
        } else if (currentScore != null) {
          setSliderValue(currentScore);
        }
      })
      .catch(() => setMessages([]))
      .finally(() => setHistoryLoaded(true));
  }, [open, companyId, valueKey]);

  useEffect(() => {
    if (open) {
      messagesEndRef.current?.scrollIntoView({ behavior: "smooth" });
    }
  }, [messages, open]);

  const handleAnalyze = async () => {
    setAnalyzing(true);
    try {
      const res = await analyzeValue(companyId, valueKey);
      setMessages((prev) => [...prev, res.message]);
      if (res.message.score_suggestion != null) {
        setSliderValue(toNum(res.message.score_suggestion));
      }
    } finally {
      setAnalyzing(false);
    }
  };

  const handleSend = async () => {
    const text = inputText.trim();
    if (!text || sending) return;
    setInputText("");
    setSending(true);
    try {
      const res = await sendChatMessage(companyId, valueKey, text);
      setMessages((prev) => [...prev, res.message]);
      if (res.message.score_suggestion != null) {
        setSliderValue(toNum(res.message.score_suggestion));
      }
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
              {isQualitative ? t.scoreLabel : "Wert"}
            </span>
            {isQualitative ? (
              <span className="font-mono text-sm font-semibold text-primary">{sliderValue.toFixed(2)}</span>
            ) : (
              <input
                type="text"
                className="w-32 rounded border border-input bg-background px-2 py-1 text-right font-mono text-sm text-foreground outline-none focus:ring-1 focus:ring-primary"
                value={sliderValue || ""}
                onChange={(e) => {
                  const v = parseFloat(e.target.value);
                  if (!isNaN(v)) setSliderValue(v);
                  else if (e.target.value === "") setSliderValue(0);
                }}
              />
            )}
          </div>
          {isQualitative && (
            <>
              <input
                type="range"
                min={0.5}
                max={1.5}
                step={0.05}
                value={sliderValue}
                onChange={(e) => setSliderValue(Number(e.target.value))}
                className="mt-2 w-full accent-primary"
              />
              <div className="flex justify-between text-[10px] text-muted-foreground">
                <span>0.50</span>
                <span>1.00</span>
                <span>1.50</span>
              </div>
            </>
          )}
        </div>

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
                onClick={handleAnalyze}
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
              {messages.map((msg) => (
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
                          Score: {toNum(msg.score_suggestion).toFixed(2)}
                        </span>
                      )}
                    </div>
                  </div>
                </div>
              ))}
              {(analyzing || sending) && (
                <div className="flex justify-start">
                  <div className="max-w-[85%] rounded-2xl rounded-bl-sm bg-muted px-4 py-3">
                    <div className="flex items-center gap-1.5">
                      <span className="h-1.5 w-1.5 animate-bounce rounded-full bg-muted-foreground/60 [animation-delay:0ms]" />
                      <span className="h-1.5 w-1.5 animate-bounce rounded-full bg-muted-foreground/60 [animation-delay:150ms]" />
                      <span className="h-1.5 w-1.5 animate-bounce rounded-full bg-muted-foreground/60 [animation-delay:300ms]" />
                    </div>
                  </div>
                </div>
              )}
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

        <footer className="shrink-0 border-t border-border px-4 py-3">
          {messages.length > 0 ? (
            <button
              onClick={async () => {
                if (accepting) return;
                setAccepting(true);
                try { await onAcceptScore(sliderValue); } finally { setAccepting(false); }
              }}
              disabled={accepting}
              className="w-full rounded-xl bg-primary py-2.5 text-sm font-medium text-primary-foreground transition-opacity hover:opacity-90 disabled:opacity-60"
            >
              {accepting ? "..." : `${t.acceptScore} (${sliderValue.toFixed(2)})`}
            </button>
          ) : (
            !historyLoaded || analyzing ? null : (
              <button
                onClick={handleAnalyze}
                disabled={analyzing}
                className="flex w-full items-center justify-center gap-2 rounded-xl bg-primary py-2.5 text-sm font-medium text-primary-foreground transition-opacity hover:opacity-90 disabled:opacity-60"
              >
                <Sparkles className="h-4 w-4" />
                {t.analyzeStart}
              </button>
            )
          )}
        </footer>
      </div>
    </div>,
    document.body
  );
}
