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

export type BatchStatus = {
  status: "idle" | "running" | "done";
  total?: number;
  done?: number;
  failed?: string[];
  current?: string[];
};

export const startFullRecompute = (portfolioId: string) =>
  api<BatchStatus>(`/api/portfolios/${portfolioId}/full-recompute`, { method: "POST" });

export const getFullRecomputeStatus = (portfolioId: string) =>
  api<BatchStatus>(`/api/portfolios/${portfolioId}/full-recompute-status`);
