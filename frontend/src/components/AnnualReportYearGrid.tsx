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

  const onPickYear = (year: number) => {
    setPendingYear(year);
    fileInputRef.current?.click();
  };

  const onFileSelected = async (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0];
    e.target.value = "";
    if (!file || pendingYear == null) return;
    setUploading(pendingYear);
    try {
      await uploadIRDocument(companyId, {
        file,
        document_type: "ANNUAL_REPORT",
        period_coverage: "FY",
        period_year: pendingYear,
        display_name: `${companyName} Annual Report ${pendingYear}`,
      });
      toast.success(`Annual Report ${pendingYear} hochgeladen`);
      await refresh();
    } catch (err) {
      const msg = (err as { message?: string })?.message;
      toast.error(msg || "Upload fehlgeschlagen");
    } finally {
      setUploading(null);
      setPendingYear(null);
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
            const isExtracting = status === "PENDING" || status === "EXTRACTING";
            const isFailed = status === "FAILED";
            const isDone = status === "DONE";
            const numExtracted = doc.extraction_results
              ? Object.values(doc.extraction_results).filter((v: unknown) => (v as { value?: unknown })?.value != null).length
              : 0;
            const colorClasses = isExtracting
              ? "border-amber-300 bg-amber-50 text-amber-800"
              : isFailed
              ? "border-red-300 bg-red-50 text-red-800"
              : "border-emerald-300 bg-emerald-50 text-emerald-800";
            const tooltipParts = [doc.display_name];
            if (isDone) tooltipParts.push(`${numExtracted}/11 Werte extrahiert`);
            if (isExtracting) tooltipParts.push("Claude analysiert PDF...");
            if (isFailed) tooltipParts.push(`Fehler: ${doc.extraction_error ?? "unbekannt"}`);
            return (
              <div key={year}
                className={`group relative flex flex-col items-center justify-center gap-0.5 rounded border px-1 py-2 ${colorClasses}`}
                title={tooltipParts.join(" — ")}
              >
                {isExtracting && <Loader2 className="h-3.5 w-3.5 animate-spin" />}
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
          return (
            <button key={year}
              onClick={() => onPickYear(year)}
              disabled={isUploading}
              className="flex flex-col items-center justify-center gap-0.5 rounded border border-dashed border-border bg-background px-1 py-2 text-muted-foreground transition-colors hover:border-primary/50 hover:bg-primary/5 hover:text-primary disabled:opacity-50"
              title={`Annual Report ${year} hochladen`}
            >
              <Upload className={`h-3.5 w-3.5 ${isUploading ? "animate-pulse" : ""}`} />
              <span className="text-[10px] font-semibold">{year}</span>
            </button>
          );
        })}
      </div>
      <input ref={fileInputRef} type="file" accept="application/pdf" onChange={onFileSelected} className="hidden" />
      <p className="mt-1.5 text-[10px] text-muted-foreground/80">
        Hochgeladene PDFs werden via Claude analysiert und liefern die Werte primaer (vor Yahoo/EDGAR).
      </p>
      <ExtraReportsList
        companyId={companyId}
        companyName={companyName}
        docs={docs}
        onChanged={refresh}
      />
    </div>
  );
}

function ExtraReportsList({
  companyId,
  companyName,
  docs,
  onChanged,
}: {
  companyId: string;
  companyName: string;
  docs: IRDocument[];
  onChanged: () => void;
}) {
  const extras = docs.filter((d) =>
    d.document_type !== "ANNUAL_REPORT" && d.document_type !== "FORM_10K" && d.document_type !== "FORM_20F"
  );
  const fileRef = useRef<HTMLInputElement>(null);
  const [busy, setBusy] = useState(false);
  const [showForm, setShowForm] = useState(false);
  const [docType, setDocType] = useState("QUARTERLY_REPORT");
  const [periodCov, setPeriodCov] = useState("Q1");
  const [periodYear, setPeriodYear] = useState(new Date().getFullYear());

  const onFile = async (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0];
    e.target.value = "";
    if (!file) return;
    setBusy(true);
    try {
      const label = `${companyName} ${docType.replace(/_/g, " ")} ${periodCov} ${periodYear}`;
      await uploadIRDocument(companyId, {
        file,
        document_type: docType,
        period_coverage: periodCov,
        period_year: periodYear,
        display_name: label,
      });
      toast.success(`${label} hochgeladen`);
      setShowForm(false);
      await onChanged();
    } catch (err) {
      toast.error((err as { message?: string })?.message || "Upload fehlgeschlagen");
    } finally {
      setBusy(false);
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
        <span className="text-[11px] font-semibold text-muted-foreground">Zusatzberichte</span>
        <button
          onClick={() => setShowForm((s) => !s)}
          className="flex items-center gap-1 rounded border border-border bg-background px-1.5 py-0.5 text-[10px] font-medium text-muted-foreground hover:bg-muted hover:text-foreground"
        >
          <Upload className="h-3 w-3" />
          {showForm ? "abbrechen" : "Quartal / Earnings hochladen"}
        </button>
      </div>
      {showForm && (
        <div className="mb-2 flex flex-wrap items-center gap-1.5 rounded border border-dashed border-border bg-muted/20 p-2 text-[11px]">
          <select value={docType} onChange={(e) => setDocType(e.target.value)} className="rounded border border-input bg-background px-1.5 py-0.5 text-[11px]">
            <option value="QUARTERLY_REPORT">Quarterly Report (10-Q)</option>
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
            disabled={busy}
            className="rounded bg-primary px-2 py-0.5 text-[11px] font-medium text-primary-foreground hover:opacity-90 disabled:opacity-50"
          >
            {busy ? "…" : "PDF wählen"}
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
