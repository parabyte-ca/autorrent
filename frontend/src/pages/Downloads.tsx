import { useEffect, useState } from "react";
import { CheckCircle2, Clock, Download, Loader2, RefreshCw, Trash2, XCircle } from "lucide-react";
import { api, type Download as DL } from "../api/client";

function fmtSize(b?: number) {
  if (!b) return "—";
  for (const [u, d] of [["TB", 1e12], ["GB", 1e9], ["MB", 1e6], ["KB", 1e3]] as [string, number][]) {
    if (b >= d) return `${(b / d).toFixed(1)} ${u}`;
  }
  return `${b} B`;
}
function fmtSpeed(bps?: number) {
  if (!bps) return null;
  return bps >= 1024 * 1024 ? `${(bps / 1024 / 1024).toFixed(1)} MB/s` : `${(bps / 1024).toFixed(0)} KB/s`;
}
function fmtEta(s?: number) {
  if (!s || s <= 0 || s > 86400 * 7) return null;
  if (s < 60) return `${s}s`;
  if (s < 3600) return `${Math.floor(s / 60)}m`;
  return `${Math.floor(s / 3600)}h ${Math.floor((s % 3600) / 60)}m`;
}
function fmtDate(s: string) {
  return new Date(s).toLocaleString(undefined, { month: "short", day: "numeric", hour: "2-digit", minute: "2-digit" });
}

function StatusBadge({ status }: { status: string }) {
  const map: Record<string, { cls: string; icon: React.ReactNode; label: string }> = {
    downloading: {
      cls: "bg-blue-100 text-blue-700 dark:bg-blue-900/40 dark:text-blue-400",
      icon: <Loader2 className="h-3 w-3 animate-spin" />, label: "Downloading",
    },
    seeding: {
      cls: "bg-green-100 text-green-700 dark:bg-green-900/40 dark:text-green-400",
      icon: <CheckCircle2 className="h-3 w-3" />, label: "Seeding",
    },
    done: {
      cls: "bg-gray-100 text-gray-600 dark:bg-gray-700 dark:text-gray-400",
      icon: <CheckCircle2 className="h-3 w-3" />, label: "Done",
    },
    adding: {
      cls: "bg-yellow-100 text-yellow-700 dark:bg-yellow-900/40 dark:text-yellow-400",
      icon: <Clock className="h-3 w-3" />, label: "Adding",
    },
    error: {
      cls: "bg-red-100 text-red-700 dark:bg-red-900/40 dark:text-red-400",
      icon: <XCircle className="h-3 w-3" />, label: "Error",
    },
  };
  const s = map[status] ?? map.adding;
  return (
    <span className={`inline-flex items-center gap-1 rounded-full px-2 py-0.5 text-xs font-medium ${s.cls}`}>
      {s.icon}{s.label}
    </span>
  );
}

function DownloadRow({ d, onDelete }: { d: DL; onDelete: (id: number) => void }) {
  const speed = fmtSpeed(d.dlspeed);
  const eta   = fmtEta(d.eta);
  const pct   = d.progress != null ? Math.round(d.progress * 100) : null;

  return (
    <li className="px-4 py-3">
      <div className="flex items-start justify-between gap-2">
        <div className="flex-1 min-w-0">
          <p className="text-sm font-medium text-gray-900 dark:text-gray-100 line-clamp-1">{d.title}</p>
          <div className="mt-1 flex flex-wrap items-center gap-3 text-xs text-gray-500 dark:text-gray-400">
            <StatusBadge status={d.status} />
            <span>{fmtSize(d.size_bytes)}</span>
            {speed && <span className="text-blue-600 dark:text-blue-400">{speed}</span>}
            {eta   && <span>ETA {eta}</span>}
            <span>{fmtDate(d.created_at)}</span>
          </div>
          {pct !== null && d.status === "downloading" && (
            <div className="mt-2">
              <div className="mb-0.5 text-xs text-gray-500 dark:text-gray-400">{pct}%</div>
              <div className="h-1.5 w-full rounded-full bg-gray-200 dark:bg-gray-700">
                <div className="h-1.5 rounded-full bg-blue-500 transition-all" style={{ width: `${pct}%` }} />
              </div>
            </div>
          )}
          {d.download_path && (
            <p className="mt-1 text-xs text-gray-400 dark:text-gray-600 truncate">{d.download_path}</p>
          )}
        </div>
        <button
          onClick={() => onDelete(d.id)}
          className="shrink-0 rounded-lg p-1.5 text-gray-400 hover:bg-red-50 dark:hover:bg-red-950 hover:text-red-500"
          title="Remove from history"
        >
          <Trash2 className="h-4 w-4" />
        </button>
      </div>
    </li>
  );
}

export default function Downloads() {
  const [downloads, setDownloads] = useState<DL[]>([]);
  const [loading,   setLoading]   = useState(true);
  const [toast,     setToast]     = useState<string | null>(null);

  const showToast = (msg: string) => { setToast(msg); setTimeout(() => setToast(null), 3000); };

  const load = async () => {
    try { setDownloads(await api.downloads.list()); }
    finally { setLoading(false); }
  };

  useEffect(() => {
    load();
    const id = setInterval(load, 10_000);
    return () => clearInterval(id);
  }, []);

  const handleDelete = async (id: number) => {
    if (!confirm("Remove this download from history?")) return;
    await api.downloads.delete(id);
    load();
    showToast("Removed from history.");
  };

  const active   = downloads.filter((d) => d.status === "downloading" || d.status === "adding");
  const finished = downloads.filter((d) => d.status !== "downloading" && d.status !== "adding");

  if (loading) return (
    <div className="flex h-full items-center justify-center">
      <Loader2 className="h-6 w-6 animate-spin text-blue-600" />
    </div>
  );

  const SectionList = ({ items }: { items: DL[] }) => (
    <div className="overflow-hidden rounded-xl border border-gray-200 dark:border-gray-700 bg-white dark:bg-gray-900 shadow-sm">
      <ul className="divide-y divide-gray-100 dark:divide-gray-800">
        {items.map((d) => <DownloadRow key={d.id} d={d} onDelete={handleDelete} />)}
      </ul>
    </div>
  );

  return (
    <div className="p-6">
      <div className="mb-6 flex items-start justify-between">
        <div>
          <h1 className="text-2xl font-bold text-gray-900 dark:text-gray-100">Downloads</h1>
          <p className="mt-1 text-sm text-gray-500 dark:text-gray-400">Live status updates every 10 seconds.</p>
        </div>
        <button onClick={load}
          className="flex items-center gap-1.5 rounded-lg border border-gray-300 dark:border-gray-600 px-3 py-2 text-sm font-medium text-gray-700 dark:text-gray-300 hover:bg-gray-100 dark:hover:bg-gray-800">
          <RefreshCw className="h-4 w-4" />
          Refresh
        </button>
      </div>

      {downloads.length === 0 ? (
        <div className="py-20 text-center text-gray-400 dark:text-gray-600">
          <Download className="mx-auto mb-3 h-12 w-12 opacity-30" />
          <p className="font-medium">No downloads yet.</p>
          <p className="mt-1 text-sm">Search for something and hit Download.</p>
        </div>
      ) : (
        <div className="space-y-6">
          {active.length > 0 && (
            <section>
              <h2 className="mb-3 text-sm font-semibold uppercase tracking-wide text-gray-500 dark:text-gray-400">
                Active ({active.length})
              </h2>
              <SectionList items={active} />
            </section>
          )}
          {finished.length > 0 && (
            <section>
              <h2 className="mb-3 text-sm font-semibold uppercase tracking-wide text-gray-500 dark:text-gray-400">
                History ({finished.length})
              </h2>
              <SectionList items={finished} />
            </section>
          )}
        </div>
      )}

      {toast && (
        <div className="fixed bottom-6 right-6 z-50 rounded-lg bg-gray-900 dark:bg-gray-700 px-4 py-3 text-sm text-white shadow-lg">
          {toast}
        </div>
      )}
    </div>
  );
}
