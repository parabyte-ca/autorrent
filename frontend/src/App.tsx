import { useEffect, useState } from "react";
import { BrowserRouter, Navigate, Route, Routes } from "react-router-dom";
import { ThemeProvider } from "./ThemeContext";
import Layout from "./components/Layout";
import Login from "./pages/Login";
import Search from "./pages/Search";
import Watchlist from "./pages/Watchlist";
import Downloads from "./pages/Downloads";
import History from "./pages/History";
import Settings from "./pages/Settings";
import { api } from "./api/client";
import { authStore } from "./auth";
import { Loader2 } from "lucide-react";

function AuthGuard({ children }: { children: React.ReactNode }) {
  const [checked, setChecked] = useState(false);
  const [needsAuth, setNeedsAuth] = useState(false);

  useEffect(() => {
    api.auth
      .status()
      .then(({ auth_required }) => {
        setNeedsAuth(auth_required);
        setChecked(true);
      })
      .catch(() => setChecked(true)); // on error, allow through
  }, []);

  if (!checked) {
    return (
      <div className="flex h-screen items-center justify-center bg-gray-50 dark:bg-gray-950">
        <Loader2 className="h-6 w-6 animate-spin text-blue-600" />
      </div>
    );
  }

  if (needsAuth && !authStore.getToken()) {
    return <Navigate to="/login" replace />;
  }

  return <>{children}</>;
}

export default function App() {
  return (
    <ThemeProvider>
      <BrowserRouter>
        <Routes>
          <Route path="/login" element={<Login />} />
          <Route
            element={
              <AuthGuard>
                <Layout />
              </AuthGuard>
            }
          >
            <Route index element={<Navigate to="/search" replace />} />
            <Route path="/search" element={<Search />} />
            <Route path="/watchlist" element={<Watchlist />} />
            <Route path="/downloads" element={<Downloads />} />
            <Route path="/history" element={<History />} />
            <Route path="/settings" element={<Settings />} />
          </Route>
        </Routes>
      </BrowserRouter>
    </ThemeProvider>
  );
}
