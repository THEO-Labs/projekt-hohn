import { useEffect, useRef, useState } from "react";
import { FileText, Check, Upload, Trash2, Download, RefreshCw, AlertTriangle, Loader2 } from "lucide-react";
import { toast } from "sonner";
import {
  listIRDocuments,
  uploadIRDocument,
  deleteIRDocument,
  downloadIRDocumentUrl,
  triggerIRDocumentExtraction,
  type IRDocument,
} from "@/api/irDocuments";
import { uploadGate, useUploadActive } from "@/lib/uploadGate";

const YEARS = [2025, 2024, 2023, 2022, 2021, 2020, 2019];

type Props = {
  companyId: string;
  companyName: string;
};

export function AnnualReportYearGrid({ companyId, companyName }: Props) {
  const [docs, setDocs] = useState<IRDocument[]>([]);
  const [uploading, setUploading] = useState<number | null>(null);
  const [deleting, setDeleting] = useState<string | null>(null);
  const fileInputRef = useRef<HTMLInputElement>(null);
  const [pendingYear, setPendingYear] = useState<number | null>(null);
  const globalUploadActive = useUploadActive();
  const blockedByOtherUpload = globalUploadActive && uploading == null;

  const refresh = () => listIRDocuments(companyId).then(setDocs).catch(() => undefined);

  useEffect(() => {
    refresh();
  }, [companyId]);

  // Poll while any doc is in PENDING/EXTRACTING state
  useEffect(() => {
    const inFlight = docs.some((d) => d.extraction_status === "PENDING" || d.extraction_status === "EXTRACTING");
    if (!inFlight) return;
    const t = setInterval(refresh, 3000);
    return () => clearInterval(t);
  }, [docs]);

  const docByYear = (year: number) =>
    docs.find((d) => d.period_coverage === "FY" && d.period_year === year &&
      (d.document_type === "ANNUAL_REPORT" || d.document_type === "FORM_10K" || d.document_type === "FORM_20F"));

  const queuedDocs = docs.filter((d) => d.extraction_status === "PENDING" || d.extraction_status === "EXTRACTING");
  const extractingDoc = docs.find((d) => d.extraction_status === "EXTRACTING");
  const anyExtracting = queuedDocs.length > 0;
  const blockReason = anyExtracting
    ? extractingDoc
      ? `Claude analysiert gerade "${extractingDoc.display_name}". ${queuedDocs.length - 1} weitere in der Warteschlange.`
      : `${queuedDocs.length} Job(s) in der Warteschlange — Claude startet gleich.`
    : null;

  const onPickYear = (year: number) => {
    setPendingYear(year);
    fileInputRef.current?.click();
  };

  const onFileSelected = async (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0];
    e.target.value = "";
    if (!file || pendingYear == null) return;
    const yr = pendingYear;
    const sizeMb = (file.size / 1024 / 1024).toFixed(1);
    setUploading(yr);
    uploadGate.start();
    const toastId = toast.loading(`Lade Annual Report ${yr} hoch (${sizeMb} MB)...`, { duration: Infinity });
    try {
      const newDoc = await uploadIRDocument(companyId, {
        file,
        document_type: "ANNUAL_REPORT",
        period_coverage: "FY",
        period_year: yr,
        display_name: `${companyName} Annual Report ${yr}`,
      });
      setDocs((prev) => [newDoc, ...prev.filter((d) => d.id !== newDoc.id)]);
      toast.success(`Annual Report ${yr} hochgeladen — eingereiht.`, { id: toastId });
      refresh();
    } catch (err) {
      const msg = (err as { message?: string })?.message;
      toast.error(msg || "Upload fehlgeschlagen", { id: toastId });
    } finally {
      setUploading(null);
      setPendingYear(null);
      uploadGate.end();
    }
  };

  const onDelete = async (doc: IRDocument) => {
    if (!confirm(`Annual Report ${doc.period_year} wirklich löschen?`)) return;
    setDeleting(doc.id);
    try {
      await deleteIRDocument(companyId, doc.id);
      toast.success("Gelöscht");
      await refresh();
    } catch {
      toast.error("Löschen fehlgeschlagen");
    } finally {
      setDeleting(null);
    }
  };

  return (
    <div className="mt-3 rounded-lg border border-border/40 bg-muted/10 p-2.5">
      <div className="mb-2 flex items-center gap-1.5 text-[11px] font-semibold text-muted-foreground">
        <FileText className="h-3.5 w-3.5" />
        Annual Reports
      </div>
      <div className="grid grid-cols-7 gap-1.5">
        {YEARS.map((year) => {
          const doc = docByYear(year);
          const isUploading = uploading === year;
          const isDeleting = deleting === doc?.id;
          if (doc) {
            const status = doc.extraction_status;
            const isPending = status === "PENDING";
            const isRunning = status === "EXTRACTING";
            const isInQueue = isPending || isRunning;
            const isFailed = status === "FAILED";
            const isDone = status === "DONE";
            const numExtracted = doc.extraction_results
              ? Object.values(doc.extraction_results).filter((v: unknown) => (v as { value?: unknown })?.value != null).length
              : 0;
            const colorClasses = isInQueue
              ? "border-amber-300 bg-amber-50 text-amber-800"
              : isFailed
              ? "border-red-300 bg-red-50 text-red-800"
              : "border-emerald-300 bg-emerald-50 text-emerald-800";
            const tooltipParts = [doc.display_name];
            if (isDone) tooltipParts.push(`${numExtracted}/11 Werte extrahiert`);
            if (isRunning) tooltipParts.push("Claude analysiert PDF...");
            if (isPending && doc.queue_position) tooltipParts.push(`Warteschlange: Position ${doc.queue_position}`);
            if (isFailed) tooltipParts.push(`Fehler: ${doc.extraction_error ?? "unbekannt"}`);
            return (
              <div key={year}
                className={`group relative flex flex-col items-center justify-center gap-0.5 rounded border px-1 py-2 ${colorClasses}`}
                title={tooltipParts.join(" — ")}
              >
                {isRunning && <Loader2 className="h-3.5 w-3.5 animate-spin" />}
                {isPending && <span className="text-[9px] font-bold">#{doc.queue_position ?? "?"}</span>}
                {isFailed && <AlertTriangle className="h-3.5 w-3.5" />}
                {isDone && <Check className="h-3.5 w-3.5" />}
                <span className="text-[10px] font-semibold">{year}</span>
                {isDone && numExtracted > 0 && (
                  <span className="text-[8px] tabular leading-none">{numExtracted}/11</span>
                )}
                <div className="absolute inset-0 flex items-center justify-center gap-1 rounded bg-white/95 opacity-0 transition-opacity group-hover:opacity-100">
                  {(isFailed || isDone) && (
                    <button onClick={async () => {
                      try {
                        await triggerIRDocumentExtraction(companyId, doc.id);
                        toast.success("Re-Extraktion gestartet");
                        await refresh();
                      } catch { toast.error("Re-Extraktion fehlgeschlagen"); }
                    }}
                      className="rounded p-0.5 text-blue-700 hover:text-blue-900"
                      title="Re-Extraktion via Claude">
                      <RefreshCw className="h-3 w-3" />
                    </button>
                  )}
                  <a href={downloadIRDocumentUrl(companyId, doc.id)} target="_blank" rel="noreferrer"
                    className="rounded p-0.5 text-emerald-800 hover:text-emerald-950"
                    title="Download">
                    <Download className="h-3 w-3" />
                  </a>
                  <button
                    onClick={() => onDelete(doc)}
                    disabled={isDeleting}
                    className="rounded p-0.5 text-red-700 hover:text-red-900 disabled:opacity-40"
                    title="Löschen"
                  >
                    <Trash2 className="h-3 w-3" />
                  </button>
                </div>
              </div>
            );
          }
          if (isUploading) {
            return (
              <div key={year}
                className="flex flex-col items-center justify-center gap-0.5 rounded border border-blue-300 bg-blue-50 px-1 py-2 text-blue-800"
                title={`Annual Report ${year} wird hochgeladen...`}
              >
                <Loader2 className="h-3.5 w-3.5 animate-spin" />
                <span className="text-[10px] font-semibold">{year}</span>
                <span className="text-[8px] leading-none">lädt...</span>
              </div>
            );
          }
          return (
            <button key={year}
              onClick={() => onPickYear(year)}
              disabled={blockedByOtherUpload}
              className="flex flex-col items-center justify-center gap-0.5 rounded border border-dashed border-border bg-background px-1 py-2 text-muted-foreground transition-colors hover:border-primary/50 hover:bg-primary/5 hover:text-primary disabled:cursor-not-allowed disabled:opacity-40 disabled:hover:border-border disabled:hover:bg-background disabled:hover:text-muted-foreground"
              title={blockedByOtherUpload ? "Anderer Upload läuft, bitte warten…" : anyExtracting ? `Annual Report ${year} hochladen — wird in Warteschlange aufgenommen.` : `Annual Report ${year} hochladen`}
            >
              <Upload className="h-3.5 w-3.5" />
              <span className="text-[10px] font-semibold">{year}</span>
            </button>
          );
        })}
      </div>
      <input ref={fileInputRef} type="file" accept="application/pdf" onChange={onFileSelected} className="hidden" />
      {anyExtracting && (
        <div className="mt-2 flex items-center gap-1.5 rounded border border-amber-300 bg-amber-50 px-2 py-1 text-[10px] text-amber-800">
          <Loader2 className="h-3 w-3 shrink-0 animate-spin" />
          <span>{blockReason} Weitere Uploads werden eingereiht und nacheinander verarbeitet.</span>
        </div>
      )}
      <p className="mt-1.5 text-[10px] text-muted-foreground/80">
        Hochgeladene PDFs werden via Claude analysiert und liefern die Werte primaer (vor Yahoo/EDGAR).
      </p>
      <QuarterlyReportGrid
        companyId={companyId}
        companyName={companyName}
        docs={docs}
        onChanged={refresh}
        anyExtracting={anyExtracting}
        blockedByOtherUpload={blockedByOtherUpload}
      />
      <ExtraReportsList
        companyId={companyId}
        companyName={companyName}
        docs={docs}
        onChanged={refresh}
        anyExtracting={anyExtracting}
      />
    </div>
  );
}


const QUARTERS = ["Q1", "Q2", "Q3", "Q4"] as const;

function QuarterlyReportGrid({
  companyId,
  companyName,
  docs,
  onChanged,
  anyExtracting,
  blockedByOtherUpload,
}: {
  companyId: string;
  companyName: string;
  docs: IRDocument[];
  onChanged: () => void;
  anyExtracting: boolean;
  blockedByOtherUpload: boolean;
}) {
  const fileInputRef = useRef<HTMLInputElement>(null);
  const [pending, setPending] = useState<{ year: number; q: typeof QUARTERS[number] } | null>(null);
  const [uploading, setUploading] = useState<string | null>(null); // "year-Q1" key

  const currentYear = new Date().getFullYear();
  const years = [currentYear - 1, currentYear];

  const docFor = (year: number, q: string) =>
    docs.find((d) => d.document_type === "QUARTERLY_REPORT" && d.period_year === year && d.period_coverage === q);

  const onPick = (year: number, q: typeof QUARTERS[number]) => {
    setPending({ year, q });
    fileInputRef.current?.click();
  };

  const onFile = async (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0];
    e.target.value = "";
    if (!file || !pending) return;
    const key = `${pending.year}-${pending.q}`;
    const label = `${companyName} ${pending.q} ${pending.year}`;
    const sizeMb = (file.size / 1024 / 1024).toFixed(1);
    setUploading(key);
    uploadGate.start();
    const toastId = toast.loading(`Lade ${label} hoch (${sizeMb} MB)...`, { duration: Infinity });
    try {
      await uploadIRDocument(companyId, {
        file,
        document_type: "QUARTERLY_REPORT",
        period_coverage: pending.q,
        period_year: pending.year,
        display_name: label,
      });
      toast.success(`${label} hochgeladen — eingereiht. Estimate FY${currentYear} wird automatisch aktualisiert.`, { id: toastId });
      await onChanged();
    } catch (err) {
      toast.error((err as { message?: string })?.message || "Upload fehlgeschlagen", { id: toastId });
    } finally {
      setUploading(null);
      setPending(null);
      uploadGate.end();
    }
  };

  return (
    <div className="mt-3 border-t border-border/30 pt-2">
      <div className="mb-2 flex items-center gap-1.5 text-[11px] font-semibold text-muted-foreground">
        <FileText className="h-3.5 w-3.5" />
        Quartalsberichte
        <span className="ml-1 text-[9px] font-normal text-muted-foreground/70">
          — werden für Faktor-Estimate FY{currentYear} verwendet
        </span>
      </div>
      <div className="space-y-1.5">
        {years.map((yr) => (
          <div key={yr} className="flex items-center gap-1.5">
            <span className="w-10 shrink-0 text-[10px] font-semibold text-muted-foreground">{yr}</span>
            <div className="grid grid-cols-4 flex-1 gap-1.5">
              {QUARTERS.map((q) => {
                const doc = docFor(yr, q);
                const tileKey = `${yr}-${q}`;
                const isUploading = uploading === tileKey;
                if (isUploading) {
                  return (
                    <div key={q}
                      className="flex flex-col items-center justify-center gap-0.5 rounded border border-blue-300 bg-blue-50 px-1 py-1.5 text-blue-800"
                    >
                      <Loader2 className="h-3 w-3 animate-spin" />
                      <span className="text-[9px] font-semibold">{q}</span>
                      <span className="text-[7px] leading-none">lädt…</span>
                    </div>
                  );
                }
                if (doc) {
                  const status = doc.extraction_status;
                  const isPending = status === "PENDING";
                  const isRunning = status === "EXTRACTING";
                  const isFailed = status === "FAILED";
                  const isDone = status === "DONE";
                  const numExtracted = doc.extraction_results
                    ? Object.values(doc.extraction_results).filter((v: unknown) => (v as { value?: unknown })?.value != null).length
                    : 0;
                  const colorClasses = (isPending || isRunning)
                    ? "border-amber-300 bg-amber-50 text-amber-800"
                    : isFailed
                    ? "border-red-300 bg-red-50 text-red-800"
                    : "border-emerald-300 bg-emerald-50 text-emerald-800";
                  const tooltipParts = [doc.display_name];
                  if (isDone) tooltipParts.push(`${numExtracted}/11 Werte`);
                  if (isRunning) tooltipParts.push("Claude analysiert...");
                  if (isPending && doc.queue_position) tooltipParts.push(`Warteschlange #${doc.queue_position}`);
                  if (isFailed) tooltipParts.push(`Fehler: ${doc.extraction_error ?? "?"}`);
                  return (
                    <div key={q}
                      className={`group relative flex flex-col items-center justify-center gap-0.5 rounded border px-1 py-1.5 ${colorClasses}`}
                      title={tooltipParts.join(" — ")}
                    >
                      {isRunning && <Loader2 className="h-3 w-3 animate-spin" />}
                      {isPending && <span className="text-[8px] font-bold">#{doc.queue_position ?? "?"}</span>}
                      {isFailed && <AlertTriangle className="h-3 w-3" />}
                      {isDone && <Check className="h-3 w-3" />}
                      <span className="text-[9px] font-semibold">{q}</span>
                      {isDone && numExtracted > 0 && (
                        <span className="text-[7px] leading-none">{numExtracted}/11</span>
                      )}
                      <div className="absolute inset-0 flex items-center justify-center gap-1 rounded bg-white/95 opacity-0 transition-opacity group-hover:opacity-100">
                        {(isFailed || isDone) && (
                          <button onClick={async () => {
                            try { await triggerIRDocumentExtraction(companyId, doc.id); toast.success("Re-Extraktion gestartet"); await onChanged(); }
                            catch { toast.error("Re-Extraktion fehlgeschlagen"); }
                          }}
                            className="rounded p-0.5 text-blue-700 hover:text-blue-900" title="Re-Extraktion">
                            <RefreshCw className="h-3 w-3" />
                          </button>
                        )}
                        <a href={downloadIRDocumentUrl(companyId, doc.id)} target="_blank" rel="noreferrer"
                          className="rounded p-0.5 text-emerald-800 hover:text-emerald-950" title="Download">
                          <Download className="h-3 w-3" />
                        </a>
                        <button onClick={async () => {
                          if (!confirm(`${q} ${yr} loeschen?`)) return;
                          try { await deleteIRDocument(companyId, doc.id); toast.success("Geloescht"); await onChanged(); }
                          catch { toast.error("Loeschen fehlgeschlagen"); }
                        }}
                          className="rounded p-0.5 text-red-700 hover:text-red-900" title="Löschen">
                          <Trash2 className="h-3 w-3" />
                        </button>
                      </div>
                    </div>
                  );
                }
                return (
                  <button key={q}
                    onClick={() => onPick(yr, q)}
                    disabled={blockedByOtherUpload}
                    className="flex flex-col items-center justify-center gap-0.5 rounded border border-dashed border-border bg-background px-1 py-1.5 text-muted-foreground transition-colors hover:border-primary/50 hover:bg-primary/5 hover:text-primary disabled:cursor-not-allowed disabled:opacity-40 disabled:hover:border-border disabled:hover:bg-background disabled:hover:text-muted-foreground"
                    title={blockedByOtherUpload ? "Anderer Upload läuft, bitte warten…" : `${q} ${yr} hochladen`}
                  >
                    <Upload className="h-3 w-3" />
                    <span className="text-[9px] font-semibold">{q}</span>
                  </button>
                );
              })}
            </div>
          </div>
        ))}
      </div>
      <input ref={fileInputRef} type="file" accept="application/pdf" onChange={onFile} className="hidden" />
      {anyExtracting && <div className="h-px" />}
    </div>
  );
}

function ExtraReportsList({
  companyId,
  companyName,
  docs,
  onChanged,
  anyExtracting,
}: {
  companyId: string;
  companyName: string;
  docs: IRDocument[];
  onChanged: () => void;
  anyExtracting: boolean;
}) {
  // Quarterlies handled by dedicated grid above; this list only shows other docs.
  const extras = docs.filter((d) =>
    d.document_type !== "ANNUAL_REPORT" && d.document_type !== "FORM_10K"
    && d.document_type !== "FORM_20F" && d.document_type !== "QUARTERLY_REPORT"
  );
  const fileRef = useRef<HTMLInputElement>(null);
  const [busy, setBusy] = useState(false);
  const [showForm, setShowForm] = useState(false);
  const [docType, setDocType] = useState("EARNINGS_RELEASE");
  const [periodCov, setPeriodCov] = useState("Q1");
  const [periodYear, setPeriodYear] = useState(new Date().getFullYear());
  const globalUploadActive = useUploadActive();
  const blockedByOther = globalUploadActive && !busy;

  const onFile = async (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0];
    e.target.value = "";
    if (!file) return;
    setBusy(true);
    uploadGate.start();
    const label = `${companyName} ${docType.replace(/_/g, " ")} ${periodCov} ${periodYear}`;
    const sizeMb = (file.size / 1024 / 1024).toFixed(1);
    const toastId = toast.loading(`Lade ${label} hoch (${sizeMb} MB)...`, { duration: Infinity });
    try {
      await uploadIRDocument(companyId, {
        file,
        document_type: docType,
        period_coverage: periodCov,
        period_year: periodYear,
        display_name: label,
      });
      toast.success(`${label} hochgeladen — eingereiht.`, { id: toastId });
      setShowForm(false);
      await onChanged();
    } catch (err) {
      toast.error((err as { message?: string })?.message || "Upload fehlgeschlagen", { id: toastId });
    } finally {
      setBusy(false);
      uploadGate.end();
    }
  };

  const onDel = async (doc: IRDocument) => {
    if (!confirm(`${doc.display_name} loeschen?`)) return;
    try { await deleteIRDocument(companyId, doc.id); toast.success("Geloescht"); await onChanged(); }
    catch { toast.error("Loeschen fehlgeschlagen"); }
  };

  return (
    <div className="mt-3 border-t border-border/30 pt-2">
      <div className="mb-1.5 flex items-center justify-between">
        <span className="text-[11px] font-semibold text-muted-foreground">Sonstige Berichte (Earnings, Präsentationen)</span>
        <button
          onClick={() => setShowForm((s) => !s)}
          disabled={blockedByOther}
          title={blockedByOther ? "Anderer Upload läuft, bitte warten…" : undefined}
          className="flex items-center gap-1 rounded border border-border bg-background px-1.5 py-0.5 text-[10px] font-medium text-muted-foreground hover:bg-muted hover:text-foreground disabled:cursor-not-allowed disabled:opacity-40 disabled:hover:bg-background disabled:hover:text-muted-foreground"
        >
          <Upload className="h-3 w-3" />
          {showForm ? "abbrechen" : "hochladen"}
        </button>
      </div>
      {showForm && (
        <div className="mb-2 flex flex-wrap items-center gap-1.5 rounded border border-dashed border-border bg-muted/20 p-2 text-[11px]">
          <select value={docType} onChange={(e) => setDocType(e.target.value)} className="rounded border border-input bg-background px-1.5 py-0.5 text-[11px]">
            <option value="EARNINGS_RELEASE">Earnings Release</option>
            <option value="INVESTOR_PRESENTATION">Investor Presentation</option>
            <option value="OTHER">Sonstiges</option>
          </select>
          <select value={periodCov} onChange={(e) => setPeriodCov(e.target.value)} className="rounded border border-input bg-background px-1.5 py-0.5 text-[11px]">
            <option value="Q1">Q1</option>
            <option value="Q2">Q2</option>
            <option value="Q3">Q3</option>
            <option value="Q4">Q4</option>
            <option value="H1">H1</option>
            <option value="H2">H2</option>
          </select>
          <input
            type="number"
            value={periodYear}
            onChange={(e) => setPeriodYear(Number(e.target.value))}
            min={2010}
            max={2030}
            className="w-16 rounded border border-input bg-background px-1.5 py-0.5 text-[11px]"
          />
          <button
            onClick={() => fileRef.current?.click()}
            disabled={busy || blockedByOther}
            title={blockedByOther ? "Anderer Upload läuft, bitte warten…" : anyExtracting ? "Wird in Warteschlange aufgenommen" : undefined}
            className="flex items-center gap-1 rounded bg-primary px-2 py-0.5 text-[11px] font-medium text-primary-foreground hover:opacity-90 disabled:cursor-not-allowed disabled:opacity-50"
          >
            {busy && <Loader2 className="h-3 w-3 animate-spin" />}
            {busy ? "lädt..." : "PDF wählen"}
          </button>
          <input ref={fileRef} type="file" accept="application/pdf" onChange={onFile} className="hidden" />
        </div>
      )}
      {extras.length === 0 ? (
        <p className="text-[10px] italic text-muted-foreground/60">Keine Zusatz-Berichte hochgeladen.</p>
      ) : (
        <div className="space-y-0.5">
          {extras.map((d) => {
            const status = d.extraction_status;
            const icon = status === "PENDING" || status === "EXTRACTING"
              ? <Loader2 className="h-3 w-3 animate-spin text-amber-600" />
              : status === "FAILED"
              ? <AlertTriangle className="h-3 w-3 text-red-600" />
              : <Check className="h-3 w-3 text-emerald-600" />;
            return (
              <div key={d.id} className="flex items-center justify-between gap-2 rounded bg-background px-1.5 py-1 text-[11px]">
                <div className="flex items-center gap-1.5 min-w-0">
                  {icon}
                  <span className="truncate font-medium text-foreground">
                    {d.document_type.replace(/_/g, " ")} {d.period_coverage} {d.period_year}
                  </span>
                  <span className="text-muted-foreground truncate">· {d.original_filename}</span>
                </div>
                <div className="flex shrink-0 items-center gap-1">
                  <a href={downloadIRDocumentUrl(companyId, d.id)} target="_blank" rel="noreferrer"
                    className="rounded p-0.5 text-muted-foreground hover:text-foreground" title="Download">
                    <Download className="h-3 w-3" />
                  </a>
                  <button onClick={() => onDel(d)} className="rounded p-0.5 text-muted-foreground hover:text-destructive" title="Loeschen">
                    <Trash2 className="h-3 w-3" />
                  </button>
                </div>
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
}
