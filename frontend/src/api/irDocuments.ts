import { api } from "./client";

export type IRDocument = {
  id: string;
  company_id: string;
  document_type: string;
  period_coverage: string;
  period_year: number;
  display_name: string;
  original_filename: string;
  size_bytes: number | null;
  mime_type: string | null;
  uploaded_at: string;
  extraction_status: "PENDING" | "EXTRACTING" | "DONE" | "FAILED";
  extracted_at: string | null;
  extraction_error: string | null;
  extraction_results?: Record<string, { value: string | null; currency?: string | null; page?: number | null; reason?: string | null }> | null;
  notes: string | null;
};

export const listIRDocuments = (companyId: string) =>
  api<IRDocument[]>(`/api/companies/${companyId}/ir-documents`);

export async function uploadIRDocument(
  companyId: string,
  payload: {
    file: File;
    document_type: string;
    period_coverage: string;
    period_year: number;
    display_name: string;
    notes?: string;
  },
): Promise<IRDocument> {
  const form = new FormData();
  form.append("file", payload.file);
  form.append("document_type", payload.document_type);
  form.append("period_coverage", payload.period_coverage);
  form.append("period_year", String(payload.period_year));
  form.append("display_name", payload.display_name);
  if (payload.notes) form.append("notes", payload.notes);
  const response = await fetch(`/api/companies/${companyId}/ir-documents`, {
    method: "POST",
    credentials: "include",
    body: form,
  });
  if (!response.ok) {
    const detail = await response.json().catch(() => null);
    throw new Error(detail?.detail || `Upload failed (${response.status})`);
  }
  return response.json();
}

export const deleteIRDocument = (companyId: string, docId: string) =>
  api<void>(`/api/companies/${companyId}/ir-documents/${docId}`, { method: "DELETE" });

export const triggerIRDocumentExtraction = (companyId: string, docId: string) =>
  api<IRDocument>(`/api/companies/${companyId}/ir-documents/${docId}/extract`, { method: "POST" });

export const downloadIRDocumentUrl = (companyId: string, docId: string) =>
  `/api/companies/${companyId}/ir-documents/${docId}/download`;
