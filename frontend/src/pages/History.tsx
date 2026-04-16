import { useEffect, useState } from "react";
import {
  AlertCircle,
  CheckCircle2,
  Clock,
  Download,
  Loader2,
  Search,
  Trash2,
  X,
  XCircle,
} from "lucide-react";
import { api, type DownloadHistoryItem } from "../api/client";

const PAGE_SIZE = 50;

// ── Helpers ───────────────────────────────────────────────────────────────────

function fmtRelative(iso: string): string {
  const ms = Date.now() - new Date(iso).getTime();
  const secs = Math.floor(ms / 1000);
  if (secs < 60) return "just now";
  if (secs < 3600) return `${Math.floor(secs / 60)}m ago`;
  if (secs < 86400) return `${Math.floor(secs / 3600)}h ago`;
  if (secs < 604800) return `${Math.floor(secs / 86400)}d ago`;
  return new Date(iso).toLocaleDateString(undefined, {
    month: "short",
    day: "numeric",
    year: "numeric",
  });
}

// ── Badges ────────────────────────────────────────────────────────────────────

function StatusBadge({ status }: { status: DownloadHistoryItem["status"] }) {
  const map: Record<DownloadHistoryItem["status"], { cls: string; icon: React.ReactNode }> = {
    completed: {
      cls: "bg-green-100 text-green-700 dark:bg-green-900/40 dark:text-green-400",
      icon: <CheckCircle2 className="h-3 w-3" />,
    },
    downloading: {
      cls: "bg-blue-100 text-blue-700 dark:bg-blue-900/40 dark:text-blue-400",
      icon: <Loader2 className="h-3 w-3 animate-spin" />,
    },
    failed: {
      cls: "bg-red-100 text-red-700 dark:bg-red-900/40 dark:text-red-400",
      icon: <XCircle className="h-3 w-3" />,
    },
  };
  const { cls, icon } = map[status];
  return (
    <span className={`inline-flex items-center gap-1 rounded-full px-2 py-0.5 text-xs font-medium ${cls}`}>
      {icon}
      {status.charAt(0).toUpperCase() + status.slice(1)}
    </span>
  );
}

function SourceBadge({ source }: { source: DownloadHistoryItem["source"] }) {
  const cls =
    source === "manual"
      ? "bg-gray-100 text-gray-600 dark:bg-gray-700 dark:text-gray-300"
      : "bg-purple-100 text-purple-700 dark:bg-purple-900/40 dark:text-purple-400";
  return (
    <span className={`inline-flex items-center rounded-full px-2 py-0.5 text-xs font-medium ${cls}`}>
      {source === "manual" ? "Manual" : "Watchlist"}
    </span>
  );
}

// ── Main page ─────────────────────────────────────────────────────────────────

export default function History() {
  const [items,        setItems]        = useState<DownloadHistoryItem[]>([]);
  const [total,        setTotal]        = useState(0);
  const [page,         setPage]         = useState(0);
  const [searchRaw,    setSearchRaw]    = useState("");
  const [searchQuery,  setSearchQuery]  = useState("");
  const [sourceFilter, setSourceFilter] = useState<"all" | "manual" | "watchlist">("all");
  const [statusFilter, setStatusFilter] = useState<"all" | "downloading" | "completed" | "failed">("all");
  const [loading,      setLoading]      = useState(true);
  const [error,        setError]        = useState<string | null>(null);
  const [exportingCsv, setExportingCsv] = useState(false);
  const [clearing,     setClearing]     = useState(false);
  const [refreshKey,   setRefreshKey]   = useState(0);
  const [toast,        setToast]        = useState<string | null>(null);

  const showToast = (msg: string) => { setToast(msg); setTimeout(() => setToast(null), 3000); };
  const refresh = () => setRefreshKey((k) => k + 1);

  // Debounce the search input 300 ms before firing a request.
  useEffect(() => {
    const t = setTimeout(() => { setSearchQuery(searchRaw); setPage(0); }, 300);
    return () => clearTimeout(t);
  }, [searchRaw]);

  // Fetch whenever any filter, page, or explicit refresh changes.
  useEffect(() => {
    let cancelled = false;
    const load = async () => {
      setLoading(true);
      setError(null);
      try {
        const params = new URLSearchParams({
          q: searchQuery,
          source: sourceFilter,
          status: statusFilter,
          skip: String(page * PAGE_SIZE),
          limit: String(PAGE_SIZE),
        });
        const data = await api.history.list(params.toString());
        if (!cancelled) { setItems(data.items); setTotal(data.total); }
      } catch (e: unknown) {
        if (!cancelled) setError((e as Error).message);
      } finally {
        if (!cancelled) setLoading(false);
      }
    };
    load();
    return () => { cancelled = true; };
  }, [searchQuery, sourceFilter, statusFilter, page, refreshKey]);

  // ── Actions ────────────────────────────────────────────────────────────────

  const handleExportCsv = async () => {
    setExportingCsv(true);
    try {
      const res = await api.history.export();
      if (!res.ok) { showToast("Export failed"); return; }
      const blob = await res.blob();
      const url = URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = url;
      const cd = res.headers.get("content-disposition") ?? "";
      const match = cd.match(/filename="([^"]+)"/);
      a.download = match?.[1] ?? `autorrent-history-${new Date().toISOString().slice(0, 10)}.csv`;
      document.body.appendChild(a);
      a.click();
      document.body.removeChild(a);
      URL.revokeObjectURL(url);
    } catch (e: unknown) {
      showToast(`Export error: ${(e as Error).message}`);
    } finally {
      setExportingCsv(false);
    }
  };

  const handleClearHistory = async () => {
    if (!confirm("Delete all download history? This cannot be undone.")) return;
    setClearing(true);
    try {
      const data = await api.history.clearAll();
      showToast(`Cleared ${data.deleted} record${data.deleted === 1 ? "" : "s"}.`);
      setPage(0);
      refresh();
    } catch (e: unknown) {
      showToast(`Error: ${(e as Error).message}`);
    } finally {
      setClearing(false);
    }
  };

  const handleDelete = async (id: number) => {
    try {
      await api.history.deleteOne(id);
      refresh();
    } catch (e: unknown) {
      showToast(`Error: ${(e as Error).message}`);
    }
  };

  // ── Pagination helpers ─────────────────────────────────────────────────────

  const totalPages = Math.ceil(total / PAGE_SIZE);
  const from = total === 0 ? 0 : page * PAGE_SIZE + 1;
  const to = Math.min((page + 1) * PAGE_SIZE, total);

  const CTRL =
    "rounded-lg border border-gray-300 dark:border-gray-600 px-2.5 py-2 text-sm " +
    "dark:bg-gray-800 dark:text-gray-100 focus:outline-none focus:ring-2 focus:ring-blue-500";

  // ── Render ─────────────────────────────────────────────────────────────────

  return (
    <div className="px-4 py-6 sm:px-6">
      {/* Header */}
      <div className="mb-6 flex flex-col gap-4 sm:flex-row sm:items-start sm:justify-between">
        <div>
          <h1 className="text-2xl font-bold text-gray-900 dark:text-gray-100">History</h1>
          <p className="mt-1 text-sm text-gray-500 dark:text-gray-400">
            All downloads initiated through AutoRrent.
          </p>
        </div>
        <div className="flex shrink-0 flex-wrap items-center gap-2">
          <button
            onClick={handleExportCsv}
            disabled={exportingCsv || total === 0}
            className="flex items-center gap-1.5 rounded-lg border border-gray-300 dark:border-gray-600 px-3 py-2 text-sm font-medium text-gray-700 dark:text-gray-300 hover:bg-gray-100 dark:hover:bg-gray-800 disabled:opacity-50"
          >
            {exportingCsv ? <Loader2 className="h-4 w-4 animate-spin" /> : <Download className="h-4 w-4" />}
            Export CSV
          </button>
          <button
            onClick={handleClearHistory}
            disabled={clearing || total === 0}
            className="flex items-center gap-1.5 rounded-lg border border-red-200 dark:border-red-900 px-3 py-2 text-sm font-medium text-red-600 dark:text-red-400 hover:bg-red-50 dark:hover:bg-red-950 disabled:opacity-50"
          >
            {clearing ? <Loader2 className="h-4 w-4 animate-spin" /> : <Trash2 className="h-4 w-4" />}
            Clear history
          </button>
        </div>
      </div>

      {/* Filters */}
      <div className="mb-4 flex flex-wrap items-center gap-3">
        {/* Search input */}
        <div className="relative min-w-[180px] flex-1">
          <Search className="absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-gray-400" />
          <input
            value={searchRaw}
            onChange={(e) => setSearchRaw(e.target.value)}
            placeholder="Search by name…"
            className="w-full rounded-lg border border-gray-300 dark:border-gray-600 py-2 pl-9 pr-8 text-sm focus:outline-none focus:ring-2 focus:ring-blue-500 dark:bg-gray-800 dark:text-gray-100 dark:placeholder-gray-500"
          />
          {searchRaw && (
            <button
              onClick={() => setSearchRaw("")}
              className="absolute right-2 top-1/2 -translate-y-1/2 rounded p-0.5 text-gray-400 hover:text-gray-600 dark:hover:text-gray-300"
            >
              <X className="h-3.5 w-3.5" />
            </button>
          )}
        </div>

        {/* Source filter */}
        <select
          value={sourceFilter}
          onChange={(e) => { setSourceFilter(e.target.value as typeof sourceFilter); setPage(0); }}
          className={CTRL}
        >
          <option value="all">All sources</option>
          <option value="manual">Manual</option>
          <option value="watchlist">Watchlist</option>
        </select>

        {/* Status filter */}
        <select
          value={statusFilter}
          onChange={(e) => { setStatusFilter(e.target.value as typeof statusFilter); setPage(0); }}
          className={CTRL}
        >
          <option value="all">All statuses</option>
          <option value="downloading">Downloading</option>
          <option value="completed">Completed</option>
          <option value="failed">Failed</option>
        </select>
      </div>

      {/* Error */}
      {error && (
        <div className="mb-4 flex items-center gap-2 rounded-lg border border-red-200 dark:border-red-900 bg-red-50 dark:bg-red-950 px-4 py-3 text-sm text-red-700 dark:text-red-400">
          <AlertCircle className="h-4 w-4 shrink-0" />
          {error}
        </div>
      )}

      {/* Content */}
      {loading ? (
        <div className="flex justify-center py-20">
          <Loader2 className="h-6 w-6 animate-spin text-blue-600" />
        </div>
      ) : items.length === 0 ? (
        <div className="py-20 text-center text-gray-400 dark:text-gray-600">
          <Clock className="mx-auto mb-3 h-12 w-12 opacity-30" />
          <p className="font-medium">No downloads recorded yet.</p>
          <p className="mt-1 text-sm">
            {searchRaw || sourceFilter !== "all" || statusFilter !== "all"
              ? "Try adjusting the filters."
              : "Downloads will appear here after you start your first torrent."}
          </p>
        </div>
      ) : (
        <>
          {/* Table */}
          <div className="overflow-x-auto rounded-xl border border-gray-200 dark:border-gray-700 bg-white dark:bg-gray-900 shadow-sm">
            <table className="min-w-full divide-y divide-gray-100 dark:divide-gray-800 text-sm">
              <thead>
                <tr className="bg-gray-50 dark:bg-gray-800/50">
                  {["Name", "Source", "Indexer", "Folder", "Size", "Added", "Completed", "Status", ""].map(
                    (h) => (
                      <th
                        key={h}
                        className="whitespace-nowrap px-4 py-3 text-left text-xs font-semibold uppercase tracking-wide text-gray-500 dark:text-gray-400"
                      >
                        {h}
                      </th>
                    )
                  )}
                </tr>
              </thead>
              <tbody className="divide-y divide-gray-100 dark:divide-gray-800">
                {items.map((item) => (
                  <tr key={item.id} className="hover:bg-gray-50 dark:hover:bg-gray-800/50">
                    {/* Name */}
                    <td className="max-w-[260px] px-4 py-3">
                      <p
                        className="line-clamp-1 font-medium text-gray-900 dark:text-gray-100"
                        title={item.name}
                      >
                        {item.name}
                      </p>
                      {item.error_msg && (
                        <p className="mt-0.5 truncate text-xs text-red-500 dark:text-red-400" title={item.error_msg}>
                          {item.error_msg}
                        </p>
                      )}
                    </td>
                    {/* Source */}
                    <td className="whitespace-nowrap px-4 py-3">
                      <SourceBadge source={item.source} />
                    </td>
                    {/* Indexer */}
                    <td className="whitespace-nowrap px-4 py-3 text-gray-500 dark:text-gray-400">
                      {item.indexer ?? "—"}
                    </td>
                    {/* Folder */}
                    <td className="whitespace-nowrap px-4 py-3 text-gray-500 dark:text-gray-400">
                      {item.folder ?? "—"}
                    </td>
                    {/* Size */}
                    <td className="whitespace-nowrap px-4 py-3 text-gray-500 dark:text-gray-400">
                      {item.size_human}
                    </td>
                    {/* Added */}
                    <td className="whitespace-nowrap px-4 py-3 text-gray-500 dark:text-gray-400">
                      {item.added_at ? fmtRelative(item.added_at) : "—"}
                    </td>
                    {/* Completed */}
                    <td className="whitespace-nowrap px-4 py-3 text-gray-500 dark:text-gray-400">
                      {item.completed_at ? fmtRelative(item.completed_at) : "—"}
                    </td>
                    {/* Status */}
                    <td className="whitespace-nowrap px-4 py-3">
                      <StatusBadge status={item.status} />
                    </td>
                    {/* Delete */}
                    <td className="px-4 py-3">
                      <button
                        onClick={() => handleDelete(item.id)}
                        title="Remove from history"
                        className="flex min-h-[44px] min-w-[44px] items-center justify-center rounded-lg text-gray-400 hover:bg-red-50 dark:hover:bg-red-950 hover:text-red-500"
                      >
                        <Trash2 className="h-4 w-4" />
                      </button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>

          {/* Pagination */}
          <div className="mt-4 flex flex-wrap items-center justify-between gap-2 text-sm text-gray-500 dark:text-gray-400">
            <span>
              {total === 0 ? "No results" : `Showing ${from}–${to} of ${total}`}
            </span>
            {totalPages > 1 && (
              <div className="flex gap-2">
                <button
                  onClick={() => setPage((p) => Math.max(0, p - 1))}
                  disabled={page === 0}
                  className="rounded-lg border border-gray-300 dark:border-gray-600 px-3 py-1.5 hover:bg-gray-100 dark:hover:bg-gray-800 disabled:opacity-40"
                >
                  Previous
                </button>
                <button
                  onClick={() => setPage((p) => Math.min(totalPages - 1, p + 1))}
                  disabled={page >= totalPages - 1}
                  className="rounded-lg border border-gray-300 dark:border-gray-600 px-3 py-1.5 hover:bg-gray-100 dark:hover:bg-gray-800 disabled:opacity-40"
                >
                  Next
                </button>
              </div>
            )}
          </div>
        </>
      )}

      {toast && (
        <div className="fixed bottom-20 right-4 z-50 rounded-lg bg-gray-900 dark:bg-gray-700 px-4 py-3 text-sm text-white shadow-lg md:bottom-6 md:right-6">
          {toast}
        </div>
      )}
    </div>
  );
}
