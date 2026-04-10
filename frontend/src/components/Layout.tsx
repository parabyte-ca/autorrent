import { NavLink, Outlet } from "react-router-dom";
import {
  Download,
  List,
  Search,
  Settings,
  Tv,
} from "lucide-react";

const nav = [
  { to: "/search",    label: "Search",     icon: Search   },
  { to: "/watchlist", label: "Watchlist",  icon: Tv       },
  { to: "/downloads", label: "Downloads",  icon: Download },
  { to: "/settings",  label: "Settings",   icon: Settings },
];

export default function Layout() {
  return (
    <div className="flex h-screen overflow-hidden bg-gray-50">
      {/* Sidebar */}
      <aside className="flex w-56 flex-col border-r border-gray-200 bg-white shadow-sm">
        {/* Logo */}
        <div className="flex items-center gap-2 px-5 py-5 border-b border-gray-100">
          <div className="flex h-8 w-8 items-center justify-center rounded-lg bg-blue-600">
            <Download className="h-4 w-4 text-white" />
          </div>
          <span className="text-lg font-bold text-gray-900">AutoRrent</span>
        </div>

        {/* Nav */}
        <nav className="flex-1 space-y-1 px-3 py-4">
          {nav.map(({ to, label, icon: Icon }) => (
            <NavLink
              key={to}
              to={to}
              className={({ isActive }) =>
                `flex items-center gap-3 rounded-lg px-3 py-2.5 text-sm font-medium transition-colors ${
                  isActive
                    ? "bg-blue-50 text-blue-700"
                    : "text-gray-600 hover:bg-gray-100 hover:text-gray-900"
                }`
              }
            >
              <Icon className="h-4 w-4 shrink-0" />
              {label}
            </NavLink>
          ))}
        </nav>

        <div className="px-4 pb-4 text-xs text-gray-400">AutoRrent v1.0</div>
      </aside>

      {/* Main */}
      <main className="flex-1 overflow-y-auto">
        <Outlet />
      </main>
    </div>
  );
}
