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

export const analyzeValue = (companyId: string, valueKey: string) =>
  api<AnalyzeResponse>(`/api/companies/${companyId}/analyze/${valueKey}`, { method: "POST" });

export const sendChatMessage = (companyId: string, valueKey: string, message: string) =>
  api<AnalyzeResponse>(`/api/companies/${companyId}/chat/${valueKey}`, {
    method: "POST",
    body: JSON.stringify({ message }),
  });

export const getChatHistory = (companyId: string, valueKey: string) =>
  api<ChatHistoryResponse>(`/api/companies/${companyId}/chat/${valueKey}/history`);
