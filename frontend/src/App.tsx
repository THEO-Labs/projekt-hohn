import { BrowserRouter, Navigate, Route, Routes } from "react-router-dom";
import { LoginPage } from "@/pages/LoginPage";
import { PortfolioListPage } from "@/pages/PortfolioListPage";
import { PortfolioDetailPage } from "@/pages/PortfolioDetailPage";
import { useAuth } from "@/hooks/useAuth";

export default function App() {
  const { user, loading, login } = useAuth();

  if (loading) {
    return (
      <div className="flex min-h-screen items-center justify-center bg-background">
        <div className="h-5 w-5 animate-spin rounded-full border-2 border-primary border-t-transparent" />
      </div>
    );
  }

  return (
    <BrowserRouter>
      <Routes>
        {!user ? (
          <>
            <Route path="/login" element={<LoginPage onLogin={async (e, p) => { await login(e, p); }} />} />
            <Route path="*" element={<Navigate to="/login" replace />} />
          </>
        ) : (
          <>
            <Route path="/" element={<PortfolioListPage />} />
            <Route path="/portfolios/:id" element={<PortfolioDetailPage />} />
            <Route path="*" element={<Navigate to="/" replace />} />
          </>
        )}
      </Routes>
    </BrowserRouter>
  );
}
