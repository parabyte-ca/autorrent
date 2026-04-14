import { useState } from "react";
import { NavLink, Outlet } from "react-router-dom";
import {
  ChevronLeft,
  ChevronRight,
  Download,
  Monitor,
  Moon,
  Search,
  Settings,
  Sun,
  Tv,
} from "lucide-react";
import { useTheme, type Theme } from "../ThemeContext";

const nav = [
  { to: "/search",    label: "Search",    icon: Search   },
  { to: "/watchlist", label: "Watchlist", icon: Tv       },
  { to: "/downloads", label: "Downloads", icon: Download },
  { to: "/settings",  label: "Settings",  icon: Settings },
];

const themeButtons: { value: Theme; icon: typeof Sun; title: string }[] = [
  { value: "light",  icon: Sun,     title: "Light theme"  },
  { value: "system", icon: Monitor, title: "System theme" },
  { value: "dark",   icon: Moon,    title: "Dark theme"   },
];

export default function Layout() {
  const { theme, setTheme } = useTheme();

  const [collapsed, setCollapsed] = useState<boolean>(() => {
    const saved = localStorage.getItem("ar-sidebar");
    if (saved !== null) return saved === "true";
    return window.innerWidth < 768;
  });

  const toggle = () =>
    setCollapsed((v) => {
      const next = !v;
      localStorage.setItem("ar-sidebar", String(next));
      return next;
    });

  return (
    <div className="flex h-screen overflow-hidden bg-gray-50 dark:bg-gray-950">
      {/* ── Sidebar (md and above) ──────────────────────────────────────────── */}
      <aside
        className={`hidden md:flex flex-col border-r border-gray-200 dark:border-gray-700 bg-white dark:bg-gray-900 shadow-sm transition-all duration-200 shrink-0 ${
          collapsed ? "w-16" : "w-56"
        }`}
      >
        {/* Logo row */}
        <div
          className={`flex items-center border-b border-gray-100 dark:border-gray-800 ${
            collapsed ? "justify-center px-0 py-5" : "gap-2 px-4 py-5"
          }`}
        >
          <div className="flex h-8 w-8 shrink-0 items-center justify-center rounded-lg bg-blue-600">
            <Download className="h-4 w-4 text-white" />
          </div>
          {!collapsed && (
            <>
              <span className="flex-1 text-lg font-bold text-gray-900 dark:text-gray-100">
                AutoRrent
              </span>
              <button
                onClick={toggle}
                title="Collapse sidebar"
                className="rounded-md p-1 text-gray-400 hover:bg-gray-100 dark:hover:bg-gray-800 hover:text-gray-600 dark:hover:text-gray-300 transition-colors"
              >
                <ChevronLeft className="h-4 w-4" />
              </button>
            </>
          )}
        </div>

        {/* Expand button (collapsed state only) */}
        {collapsed && (
          <button
            onClick={toggle}
            title="Expand sidebar"
            className="mx-auto mt-2 rounded-md p-1.5 text-gray-400 hover:bg-gray-100 dark:hover:bg-gray-800 hover:text-gray-600 dark:hover:text-gray-300 transition-colors"
          >
            <ChevronRight className="h-4 w-4" />
          </button>
        )}

        {/* Navigation */}
        <nav className={`flex-1 space-y-1 py-4 ${collapsed ? "px-2" : "px-3"}`}>
          {nav.map(({ to, label, icon: Icon }) => (
            <NavLink
              key={to}
              to={to}
              title={collapsed ? label : undefined}
              className={({ isActive }) =>
                `flex items-center rounded-lg py-2.5 text-sm font-medium transition-colors ${
                  collapsed ? "justify-center px-0" : "gap-3 px-3"
                } ${
                  isActive
                    ? "bg-blue-50 dark:bg-blue-950 text-blue-700 dark:text-blue-400"
                    : "text-gray-600 dark:text-gray-400 hover:bg-gray-100 dark:hover:bg-gray-800 hover:text-gray-900 dark:hover:text-gray-100"
                }`
              }
            >
              <Icon className="h-4 w-4 shrink-0" />
              {!collapsed && label}
            </NavLink>
          ))}
        </nav>

        {/* Footer: theme toggle + version */}
        <div
          className={`pb-4 ${
            collapsed ? "flex flex-col items-center gap-1.5 px-2" : "px-4"
          }`}
        >
          <div className={`flex ${collapsed ? "flex-col gap-1" : "gap-1 mb-2"}`}>
            {themeButtons.map(({ value, icon: Icon, title }) => (
              <button
                key={value}
                title={title}
                onClick={() => setTheme(value)}
                className={`rounded-md p-1.5 transition-colors ${
                  theme === value
                    ? "bg-blue-100 dark:bg-blue-900 text-blue-700 dark:text-blue-400"
                    : "text-gray-400 dark:text-gray-500 hover:bg-gray-100 dark:hover:bg-gray-800 hover:text-gray-600 dark:hover:text-gray-300"
                }`}
              >
                <Icon className="h-3.5 w-3.5" />
              </button>
            ))}
          </div>
          {!collapsed && (
            <p className="text-xs text-gray-400 dark:text-gray-600">AutoRrent v1.2</p>
          )}
        </div>
      </aside>

      {/* ── Main content ────────────────────────────────────────────────────── */}
      {/* main-scroll: on mobile adds padding to clear the bottom tab bar     */}
      {/* + iOS safe area; on md+ resets to zero (see index.css)              */}
      <main className="flex-1 overflow-y-auto main-scroll">
        <Outlet />
      </main>

      {/* ── Bottom tab bar (mobile only, hidden on md+) ─────────────────────── */}
      <nav
        className="fixed bottom-0 left-0 right-0 z-40 flex md:hidden bg-white dark:bg-gray-900 border-t border-gray-200 dark:border-gray-700 shadow-[0_-1px_3px_rgba(0,0,0,0.08)]"
        style={{ paddingBottom: "env(safe-area-inset-bottom, 0px)" } as React.CSSProperties}
      >
        {nav.map(({ to, label, icon: Icon }) => (
          <NavLink
            key={to}
            to={to}
            className={({ isActive }) =>
              `flex flex-1 flex-col items-center justify-center gap-0.5 py-2 text-[11px] font-medium min-h-[52px] transition-colors ${
                isActive
                  ? "text-blue-600 dark:text-blue-400"
                  : "text-gray-500 dark:text-gray-400"
              }`
            }
          >
            <Icon className="h-5 w-5" />
            {label}
          </NavLink>
        ))}
      </nav>
    </div>
  );
}
