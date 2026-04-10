import { useEffect, useState } from "react";
import {
  AlertCircle,
  CheckCircle2,
  Clock,
  Edit2,
  Loader2,
  Play,
  Plus,
  RefreshCw,
  Trash2,
  Tv,
  XCircle,
} from "lucide-react";
import { api, type DownloadPath, type WatchlistCreate, type WatchlistItem } from "../api/client";

const QUALITIES = ["4K", "1080p", "720p", "480p", "Any"];
const CODECS = ["x265", "x264", "AV1", "Any"];

function fmtDate(d?: string) {
  if (!d) return "Never";
  return new Date(d).toLocaleString(undefined, {
    month: "short",
    day: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  });
}

function epLabel(season: number, episode: number) {
  return `S${String(season).padStart(2, "0")}E${String(episode).padStart(2, "0")}`;
}

// ── Form modal ────────────────────────────────────────────────────────────────

interface FormModalProps {
  initial?: WatchlistItem;
  paths: DownloadPath[];
  onClose: () => void;
  onSave: (data: WatchlistCreate) => void;
}

function FormModal({ initial, paths, onClose, onSave }: FormModalProps) {
  const defaultPath = paths.find((p) => p.is_default);
  const [form, setForm] = useState<WatchlistCreate>(
    initial
      ? {
          title: initial.title,
          search_query: initial.search_query,
          quality: initial.quality,
          codec: initial.codec ?? "x265",
          season: initial.season,
          episode: initial.episode,
          download_path_id: initial.download_path_id,
          enabled: initial.enabled,
        }
      : {
          title: "",
          search_query: "",
          quality: "1080p",
          codec: "x265",
          season: 1,
          episode: 1,
          download_path_id: defaultPath?.id,
          enabled: true,
        }
  );

  const set = (k: keyof WatchlistCreate, v: unknown) =>
    setForm((f) => ({ ...f, [k]: v }));

  const valid = form.title.trim() && form.search_query.trim();

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/40 p-4">
      <div className="w-full max-w-md rounded-xl bg-white p-6 shadow-xl">
        <h2 className="mb-4 text-lg font-semibold text-gray-900">
          {initial ? "Edit watchlist item" : "Add to watchlist"}
        </h2>

        <div className="space-y-3">
          <div>
            <label className="mb-1 block text-sm font-medium text-gray-700">Show title</label>
            <input
              value={form.title}
              onChange={(e) => set("title", e.target.value)}
              placeholder="e.g. Breaking Bad"
              className="w-full rounded-lg border border-gray-300 px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-blue-500"
            />
          </div>
          <div>
            <label className="mb-1 block text-sm font-medium text-gray-700">
              Search query{" "}
              <span className="text-xs text-gray-400 font-normal">
                (what to search for, e.g. "Breaking Bad")
              </span>
            </label>
            <input
              value={form.search_query}
              onChange={(e) => set("search_query", e.target.value)}
              placeholder="e.g. Breaking Bad"
              className="w-full rounded-lg border border-gray-300 px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-blue-500"
            />
          </div>
          <div className="flex gap-3">
            <div className="flex-1">
              <label className="mb-1 block text-sm font-medium text-gray-700">Season</label>
              <input
                type="number"
                min={1}
                value={form.season}
                onChange={(e) => set("season", Number(e.target.value))}
                className="w-full rounded-lg border border-gray-300 px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-blue-500"
              />
            </div>
            <div className="flex-1">
              <label className="mb-1 block text-sm font-medium text-gray-700">
                Next episode
              </label>
              <input
                type="number"
                min={1}
                value={form.episode}
                onChange={(e) => set("episode", Number(e.target.value))}
                className="w-full rounded-lg border border-gray-300 px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-blue-500"
              />
            </div>
          </div>
          <div className="flex gap-3">
            <div className="flex-1">
              <label className="mb-1 block text-sm font-medium text-gray-700">Quality</label>
              <select
                value={form.quality}
                onChange={(e) => set("quality", e.target.value)}
                className="w-full rounded-lg border border-gray-300 px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-blue-500"
              >
                {QUALITIES.map((q) => (
                  <option key={q}>{q}</option>
                ))}
              </select>
            </div>
            <div className="flex-1">
              <label className="mb-1 block text-sm font-medium text-gray-700">Codec</label>
              <select
                value={form.codec}
                onChange={(e) => set("codec", e.target.value)}
                className="w-full rounded-lg border border-gray-300 px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-blue-500"
              >
                {CODECS.map((c) => (
                  <option key={c}>{c}</option>
                ))}
              </select>
            </div>
          </div>
          {paths.length > 0 && (
            <div>
              <label className="mb-1 block text-sm font-medium text-gray-700">Save to</label>
              <select
                value={form.download_path_id ?? ""}
                onChange={(e) =>
                  set("download_path_id", e.target.value ? Number(e.target.value) : undefined)
                }
                className="w-full rounded-lg border border-gray-300 px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-blue-500"
              >
                <option value="">Use default</option>
                {paths.map((p) => (
                  <option key={p.id} value={p.id}>
                    {p.name}
                  </option>
                ))}
              </select>
            </div>
          )}
        </div>

        <div className="mt-5 flex gap-3">
          <button
            onClick={onClose}
            className="flex-1 rounded-lg border border-gray-300 px-4 py-2 text-sm font-medium text-gray-700 hover:bg-gray-50"
          >
            Cancel
          </button>
          <button
            onClick={() => valid && onSave(form)}
            disabled={!valid}
            className="flex-1 rounded-lg bg-blue-600 px-4 py-2 text-sm font-medium text-white hover:bg-blue-700 disabled:opacity-50"
          >
            {initial ? "Save changes" : "Add"}
          </button>
        </div>
      </div>
    </div>
  );
}

// ── Main page ─────────────────────────────────────────────────────────────────

export default function Watchlist() {
  const [items, setItems] = useState<WatchlistItem[]>([]);
  const [paths, setPaths] = useState<DownloadPath[]>([]);
  const [loading, setLoading] = useState(true);
  const [scanning, setScanning] = useState<number | "all" | null>(null);
  const [modal, setModal] = useState<WatchlistItem | true | null>(null);
  const [toast, setToast] = useState<string | null>(null);

  const showToast = (msg: string) => {
    setToast(msg);
    setTimeout(() => setToast(null), 3000);
  };

  const load = async () => {
    setLoading(true);
    try {
      const [w, p] = await Promise.all([api.watchlist.list(), api.paths.list()]);
      setItems(w);
      setPaths(p);
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => { load(); }, []);

  const handleSave = async (data: WatchlistCreate) => {
    if (modal === true) {
      await api.watchlist.create(data);
      showToast("Added!");
    } else if (modal && typeof modal === "object") {
      await api.watchlist.update(modal.id, data);
      showToast("Updated!");
    }
    setModal(null);
    load();
  };

  const handleDelete = async (id: number) => {
    if (!confirm("Remove this show from the watchlist?")) return;
    await api.watchlist.delete(id);
    load();
  };

  const handleToggle = async (item: WatchlistItem) => {
    await api.watchlist.update(item.id, { enabled: !item.enabled });
    load();
  };

  const handleScan = async (id?: number) => {
    setScanning(id ?? "all");
    try {
      if (id) {
        await api.watchlist.scan(id);
      } else {
        await api.watchlist.scanAll();
      }
      showToast("Scan triggered — results will appear shortly.");
    } finally {
      setScanning(null);
    }
  };

  const pathName = (id?: number) => paths.find((p) => p.id === id)?.name ?? "Default";

  if (loading) {
    return (
      <div className="flex h-full items-center justify-center">
        <Loader2 className="h-6 w-6 animate-spin text-blue-600" />
      </div>
    );
  }

  return (
    <div className="p-6">
      <div className="mb-6 flex items-start justify-between">
        <div>
          <h1 className="text-2xl font-bold text-gray-900">Watchlist</h1>
          <p className="mt-1 text-sm text-gray-500">
            AutoRrent scans these shows and downloads new episodes automatically.
          </p>
        </div>
        <div className="flex gap-2">
          <button
            onClick={() => handleScan()}
            disabled={scanning !== null}
            className="flex items-center gap-1.5 rounded-lg border border-gray-300 px-3 py-2 text-sm font-medium text-gray-700 hover:bg-gray-100 disabled:opacity-50"
          >
            {scanning === "all" ? (
              <Loader2 className="h-4 w-4 animate-spin" />
            ) : (
              <RefreshCw className="h-4 w-4" />
            )}
            Scan all
          </button>
          <button
            onClick={() => setModal(true)}
            className="flex items-center gap-1.5 rounded-lg bg-blue-600 px-4 py-2 text-sm font-medium text-white hover:bg-blue-700"
          >
            <Plus className="h-4 w-4" />
            Add show
          </button>
        </div>
      </div>

      {items.length === 0 ? (
        <div className="py-20 text-center text-gray-400">
          <Tv className="mx-auto mb-3 h-12 w-12 opacity-30" />
          <p className="font-medium">No shows on your watchlist yet.</p>
          <p className="mt-1 text-sm">Click "Add show" or use the Search page.</p>
        </div>
      ) : (
        <div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-3">
          {items.map((item) => (
            <div
              key={item.id}
              className={`rounded-xl border bg-white p-4 shadow-sm transition-opacity ${
                item.enabled ? "" : "opacity-60"
              }`}
            >
              {/* Header */}
              <div className="mb-3 flex items-start justify-between gap-2">
                <div>
                  <h3 className="font-semibold text-gray-900 leading-tight">{item.title}</h3>
                  <p className="text-xs text-gray-500 mt-0.5">
                    Looking for: {epLabel(item.season, item.episode)}
                  </p>
                </div>
                {/* Enable/disable toggle */}
                <button
                  onClick={() => handleToggle(item)}
                  title={item.enabled ? "Disable" : "Enable"}
                  className={`mt-0.5 rounded-full p-1 transition-colors ${
                    item.enabled
                      ? "text-green-500 hover:text-green-700"
                      : "text-gray-300 hover:text-gray-500"
                  }`}
                >
                  {item.enabled ? (
                    <CheckCircle2 className="h-5 w-5" />
                  ) : (
                    <XCircle className="h-5 w-5" />
                  )}
                </button>
              </div>

              {/* Details */}
              <div className="mb-3 space-y-1 text-xs text-gray-500">
                <div className="flex gap-4">
                  <span>Quality: <strong className="text-gray-700">{item.quality}</strong></span>
                  <span>Codec: <strong className="text-gray-700">{item.codec ?? "x265"}</strong></span>
                  <span>Save to: <strong className="text-gray-700">{pathName(item.download_path_id)}</strong></span>
                </div>
                <div className="flex items-center gap-1">
                  <Clock className="h-3 w-3" />
                  Checked: {fmtDate(item.last_checked ?? undefined)}
                </div>
                {item.last_found && (
                  <div className="flex items-center gap-1 text-green-600">
                    <CheckCircle2 className="h-3 w-3" />
                    Last found: {fmtDate(item.last_found)}
                  </div>
                )}
              </div>

              {/* Actions */}
              <div className="flex gap-2">
                <button
                  onClick={() => handleScan(item.id)}
                  disabled={scanning !== null}
                  className="flex flex-1 items-center justify-center gap-1.5 rounded-lg border border-gray-300 py-1.5 text-xs font-medium text-gray-600 hover:bg-gray-100 disabled:opacity-50"
                >
                  {scanning === item.id ? (
                    <Loader2 className="h-3.5 w-3.5 animate-spin" />
                  ) : (
                    <Play className="h-3.5 w-3.5" />
                  )}
                  Scan now
                </button>
                <button
                  onClick={() => setModal(item)}
                  className="rounded-lg border border-gray-300 px-2.5 py-1.5 text-xs text-gray-600 hover:bg-gray-100"
                  title="Edit"
                >
                  <Edit2 className="h-3.5 w-3.5" />
                </button>
                <button
                  onClick={() => handleDelete(item.id)}
                  className="rounded-lg border border-red-200 px-2.5 py-1.5 text-xs text-red-500 hover:bg-red-50"
                  title="Delete"
                >
                  <Trash2 className="h-3.5 w-3.5" />
                </button>
              </div>
            </div>
          ))}
        </div>
      )}

      {modal !== null && (
        <FormModal
          initial={modal === true ? undefined : modal}
          paths={paths}
          onClose={() => setModal(null)}
          onSave={handleSave}
        />
      )}

      {toast && (
        <div className="fixed bottom-6 right-6 z-50 rounded-lg bg-gray-900 px-4 py-3 text-sm text-white shadow-lg">
          {toast}
        </div>
      )}
    </div>
  );
}
