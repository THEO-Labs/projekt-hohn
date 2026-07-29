import { api } from "./client";

export type Portfolio = {
  id: string;
  name: string;
  created_at: string;
  updated_at: string;
};

export const listPortfolios = () => api<Portfolio[]>("/api/portfolios");
export const createPortfolio = (name: string) =>
  api<Portfolio>("/api/portfolios", { method: "POST", body: JSON.stringify({ name }) });
export const deletePortfolio = (id: string) =>
  api<void>(`/api/portfolios/${id}`, { method: "DELETE" });

export type PortfolioMember = {
  user_id: string;
  email: string;
  is_owner: boolean;
};

export const listMembers = (portfolioId: string) =>
  api<PortfolioMember[]>(`/api/portfolios/${portfolioId}/members`);
export const addMember = (portfolioId: string, email: string) =>
  api<PortfolioMember>(`/api/portfolios/${portfolioId}/members`, {
    method: "POST",
    body: JSON.stringify({ email }),
  });
export const removeMember = (portfolioId: string, userId: string) =>
  api<void>(`/api/portfolios/${portfolioId}/members/${userId}`, { method: "DELETE" });

export type BatchStatus = {
  status: "idle" | "running" | "done";
  mode?: "full" | "stale_only";
  total?: number;
  done?: number;
  failed?: string[];
  current?: string[];
  // Nur in der Start-Response von smart-recompute: Ticker der ausgewaehlten Firmen.
  selected?: string[];
};

export const startFullRecompute = (portfolioId: string) =>
  api<BatchStatus>(`/api/portfolios/${portfolioId}/full-recompute`, { method: "POST" });

export const startSmartRecompute = (portfolioId: string) =>
  api<BatchStatus>(`/api/portfolios/${portfolioId}/smart-recompute`, { method: "POST" });

export const getFullRecomputeStatus = (portfolioId: string) =>
  api<BatchStatus>(`/api/portfolios/${portfolioId}/full-recompute-status`);
