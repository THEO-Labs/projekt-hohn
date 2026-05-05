import { useEffect, useRef, useState } from "react";
import { FileText, Check, Upload, Trash2, Download } from "lucide-react";
import { toast } from "sonner";
import {
  listIRDocuments,
  uploadIRDocument,
  deleteIRDocument,
  downloadIRDocumentUrl,
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
            return (
              <div key={year}
                className="group relative flex flex-col items-center justify-center gap-0.5 rounded border border-emerald-300 bg-emerald-50 px-1 py-2 text-emerald-800"
                title={doc.original_filename}
              >
                <Check className="h-3.5 w-3.5" />
                <span className="text-[10px] font-semibold">{year}</span>
                <div className="absolute inset-0 flex items-center justify-center gap-1 rounded bg-emerald-100/95 opacity-0 transition-opacity group-hover:opacity-100">
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
    </div>
  );
}
