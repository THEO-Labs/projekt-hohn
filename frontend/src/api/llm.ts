import { api } from "./client";

export type LlmMessage = {
  id: string;
  role: "user" | "assistant" | "system";
  content: string;
  score_suggestion: number | null;
  created_at: string;
};

export type AnalyzeResponse = {
  conversation_id: string;
  message: LlmMessage;
};

export type ChatHistoryResponse = {
  conversation_id: string;
  messages: LlmMessage[];
};

export const analyzeValue = (companyId: string, valueKey: string, periodType?: string, periodYear?: number, force = false) => {
  const params = new URLSearchParams();
  if (periodType) params.set("period_type", periodType);
  if (periodYear) params.set("period_year", String(periodYear));
  if (force) params.set("force", "true");
  const qs = params.toString();
  return api<AnalyzeResponse>(`/api/companies/${companyId}/analyze/${valueKey}${qs ? `?${qs}` : ""}`, { method: "POST" });
};

export const sendChatMessage = (companyId: string, valueKey: string, message: string, periodType?: string, periodYear?: number) => {
  const params = new URLSearchParams();
  if (periodType) params.set("period_type", periodType);
  if (periodYear) params.set("period_year", String(periodYear));
  const qs = params.toString();
  return api<AnalyzeResponse>(`/api/companies/${companyId}/chat/${valueKey}${qs ? `?${qs}` : ""}`, {
    method: "POST",
    body: JSON.stringify({ message }),
  });
};

export const getChatHistory = (companyId: string, valueKey: string) =>
  api<ChatHistoryResponse>(`/api/companies/${companyId}/chat/${valueKey}/history`);
