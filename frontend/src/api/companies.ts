import { api } from "./client";

export type Company = {
  id: string;
  portfolio_id: string;
  name: string;
  ticker: string;
  isin: string | null;
  currency: string;
  created_at: string;
  updated_at: string;
};

export type CompanyCreate = {
  name: string;
  ticker: string;
  isin?: string;
  currency: string;
};

export const listCompanies = (portfolioId: string) =>
  api<Company[]>(`/api/portfolios/${portfolioId}/companies`);

export const createCompany = (portfolioId: string, payload: CompanyCreate) =>
  api<Company>(`/api/portfolios/${portfolioId}/companies`, {
    method: "POST",
    body: JSON.stringify(payload),
  });

export const updateCompany = (id: string, payload: Partial<CompanyCreate>) =>
  api<Company>(`/api/companies/${id}`, { method: "PATCH", body: JSON.stringify(payload) });

export const deleteCompany = (id: string) =>
  api<void>(`/api/companies/${id}`, { method: "DELETE" });
