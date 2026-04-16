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
export const updatePortfolio = (id: string, name: string) =>
  api<Portfolio>(`/api/portfolios/${id}`, { method: "PATCH", body: JSON.stringify({ name }) });
export const deletePortfolio = (id: string) =>
  api<void>(`/api/portfolios/${id}`, { method: "DELETE" });
