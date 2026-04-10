import { useState, useRef } from "react";
import {
  AlertCircle,
  Download,
  ExternalLink,
  Loader2,
  Plus,
  Search as SearchIcon,
  Users,
} from "lucide-react";
import { api, type DownloadPath, type TorrentResult, type WatchlistCreate } from "../api/client";

const QUALITIES = ["Any", "4K", "1080p", "720p", "480p"];
const INDEXERS = [
  { value: "all",    label: "All sources" },
  { value: "nyaa",   label: "NYAA" },
  { value: "tpb",    label: "The Pirate Bay" },
  { value: "jackett", label: "Jackett" },
];

const SOURCE_COLORS: Record<string, string> = {
  nyaa:    "bg-purple-100 text-purple-700",
  tpb:     "bg-orange-100 text-orange-700",
  jackett: "bg-green-100 text-green-700",
};

const QUALITY_COLORS: Record<string, string> = {
  "4K":      "bg-yellow-100 text-yellow-700",
  "1080p":   "bg-blue-100 text-blue-700",
  "720p":    "bg-sky-100 text-sky-700",
  "480p":    "bg-gray-100 text-gray-700",
  "Unknown": "bg-gray-100 text-gray-500",
};

function sourceLabel(source: string) {
  if (source.startsWith("jackett/")) return source.replace("jackett/", "");
  return source.toUpperCase();
}

function sourceBadgeClass(source: string) {
  const key = source.startsWith("jackett") ? "jackett" : source;
  return SOURCE_COLORS[key] ?? "bg-gray-100 text-gray-600";
}

function fmtSpeed(bps: number) {
  if (bps >= 1024 * 1024) return `${(bps / 1024 / 1024).toFixed(1)} MB/s`;
  return `${(bps / 1024).toFixed(0)} KB/s`;
}

// ── Download modal ────────────────────────────────────────────────────────────

interface DownloadModalProps {
  result: TorrentResult;
  paths: DownloadPath[];
  onClose: () => void;
  onConfirm: (pathId?: number) => void;
}

function DownloadModal({ result, paths, onClose, onConfirm }: DownloadModalProps) {
  const defaultPath = paths.find((p) => p.is_default);
  const [pathId, setPathId] = useState<number | undefined>(defaultPath?.id);

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/40 p-4">
      <div className="w-full max-w-md rounded-xl bg-white p-6 shadow-xl">
        <h2 className="mb-1 text-lg font-semibold text-gray-900">Download</h2>
        <p className="mb-4 text-sm text-gray-500 line-clamp-2">{result.title}</p>

        <label className="mb-1 block text-sm font-medium text-gray-700">Save to</label>
        {paths.length === 0 ? (
          <p className="mb-4 text-sm text-amber-600">
            No download paths configured. Add one in Settings first.
          </p>
        ) : (
          <select
            value={pathId ?? ""}
            onChange={(e) => setPathId(e.target.value ? Number(e.target.value) : undefined)}
            className="mb-4 w-full rounded-lg border border-gray-300 px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-blue-500"
          >
            {paths.map((p) => (
              <option key={p.id} value={p.id}>
                {p.name} — {p.path}
              </option>
            ))}
          </select>
        )}

        <div className="flex gap-3">
          <button
            onClick={onClose}
            className="flex-1 rounded-lg border border-gray-300 px-4 py-2 text-sm font-medium text-gray-700 hover:bg-gray-50"
          >
            Cancel
          </button>
          <button
            onClick={() => onConfirm(pathId)}
            disabled={paths.length === 0}
            className="flex-1 rounded-lg bg-blue-600 px-4 py-2 text-sm font-medium text-white hover:bg-blue-700 disabled:opacity-50"
          >
            Download
          </button>
        </div>
      </div>
    </div>
  );
}

// ── Watchlist modal ───────────────────────────────────────────────────────────

interface WatchlistModalProps {
  result: TorrentResult;
  paths: DownloadPath[];
  onClose: () => void;
  onConfirm: (data: WatchlistCreate) => void;
}

function WatchlistModal({ result, paths, onClose, onConfirm }: WatchlistModalProps) {
  const defaultPath = paths.find((p) => p.is_default);
  const [form, setForm] = useState<WatchlistCreate>({
    title: result.title.replace(/S\d+E\d+.*/i, "").trim(),
    search_query: result.title.replace(/S\d+E\d+.*/i, "").trim(),
    quality: result.quality && result.quality !== "Unknown" ? result.quality : "1080p",
    season: 1,
    episode: 1,
    download_path_id: defaultPath?.id,
    enabled: true,
  });

  const set = (k: keyof WatchlistCreate, v: unknown) =>
    setForm((f) => ({ ...f, [k]: v }));

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/40 p-4">
      <div className="w-full max-w-md rounded-xl bg-white p-6 shadow-xl">
        <h2 className="mb-4 text-lg font-semibold text-gray-900">Add to Watchlist</h2>

        <div className="space-y-3">
          <div>
            <label className="mb-1 block text-sm font-medium text-gray-700">Show title</label>
            <input
              value={form.title}
              onChange={(e) => set("title", e.target.value)}
              className="w-full rounded-lg border border-gray-300 px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-blue-500"
            />
          </div>
          <div>
            <label className="mb-1 block text-sm font-medium text-gray-700">
              Search query{" "}
              <span className="text-gray-400 font-normal">(what to search for)</span>
            </label>
            <input
              value={form.search_query}
              onChange={(e) => set("search_query", e.target.value)}
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
          <div>
            <label className="mb-1 block text-sm font-medium text-gray-700">
              Preferred quality
            </label>
            <select
              value={form.quality}
              onChange={(e) => set("quality", e.target.value)}
              className="w-full rounded-lg border border-gray-300 px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-blue-500"
            >
              {QUALITIES.filter((q) => q !== "Any").map((q) => (
                <option key={q}>{q}</option>
              ))}
            </select>
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
                    {p.name} — {p.path}
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
            onClick={() => onConfirm(form)}
            className="flex-1 rounded-lg bg-blue-600 px-4 py-2 text-sm font-medium text-white hover:bg-blue-700"
          >
            Add to Watchlist
          </button>
        </div>
      </div>
    </div>
  );
}

// ── Main page ─────────────────────────────────────────────────────────────────

export default function Search() {
  const [query, setQuery] = useState("");
  const [quality, setQuality] = useState("Any");
  const [indexer, setIndexer] = useState("all");
  const [results, setResults] = useState<TorrentResult[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [paths, setPaths] = useState<DownloadPath[]>([]);
  const [downloadTarget, setDownloadTarget] = useState<TorrentResult | null>(null);
  const [watchlistTarget, setWatchlistTarget] = useState<TorrentResult | null>(null);
  const [toast, setToast] = useState<string | null>(null);
  const inputRef = useRef<HTMLInputElement>(null);

  const showToast = (msg: string) => {
    setToast(msg);
    setTimeout(() => setToast(null), 3000);
  };

  const doSearch = async () => {
    if (!query.trim()) return;
    setLoading(true);
    setError(null);
    try {
      const [res, p] = await Promise.all([
        api.search(query, indexer, quality),
        api.paths.list(),
      ]);
      setResults(res);
      setPaths(p);
    } catch (e: unknown) {
      setError((e as Error).message);
    } finally {
      setLoading(false);
    }
  };

  const handleDownload = async (pathId?: number) => {
    if (!downloadTarget) return;
    try {
      await api.downloads.add({
        magnet: downloadTarget.magnet,
        title: downloadTarget.title,
        download_path_id: pathId,
      });
      showToast("Added to qBittorrent!");
    } catch (e: unknown) {
      showToast(`Error: ${(e as Error).message}`);
    }
    setDownloadTarget(null);
  };

  const handleWatchlist = async (data: WatchlistCreate) => {
    try {
      await api.watchlist.create(data);
      showToast(`"${data.title}" added to watchlist!`);
    } catch (e: unknown) {
      showToast(`Error: ${(e as Error).message}`);
    }
    setWatchlistTarget(null);
  };

  return (
    <div className="p-6">
      <div className="mb-6">
        <h1 className="text-2xl font-bold text-gray-900">Search Torrents</h1>
        <p className="text-sm text-gray-500 mt-1">
          Find torrents and send them straight to qBittorrent.
        </p>
      </div>

      {/* Search bar */}
      <div className="mb-4 flex flex-col gap-3 sm:flex-row">
        <input
          ref={inputRef}
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          onKeyDown={(e) => e.key === "Enter" && doSearch()}
          placeholder="Search for a show, movie, or episode…"
          className="flex-1 rounded-lg border border-gray-300 px-4 py-2.5 text-sm shadow-sm focus:outline-none focus:ring-2 focus:ring-blue-500"
        />
        <select
          value={quality}
          onChange={(e) => setQuality(e.target.value)}
          className="rounded-lg border border-gray-300 px-3 py-2.5 text-sm focus:outline-none focus:ring-2 focus:ring-blue-500"
        >
          {QUALITIES.map((q) => (
            <option key={q}>{q}</option>
          ))}
        </select>
        <select
          value={indexer}
          onChange={(e) => setIndexer(e.target.value)}
          className="rounded-lg border border-gray-300 px-3 py-2.5 text-sm focus:outline-none focus:ring-2 focus:ring-blue-500"
        >
          {INDEXERS.map((i) => (
            <option key={i.value} value={i.value}>
              {i.label}
            </option>
          ))}
        </select>
        <button
          onClick={doSearch}
          disabled={loading}
          className="flex items-center gap-2 rounded-lg bg-blue-600 px-5 py-2.5 text-sm font-medium text-white hover:bg-blue-700 disabled:opacity-60"
        >
          {loading ? (
            <Loader2 className="h-4 w-4 animate-spin" />
          ) : (
            <SearchIcon className="h-4 w-4" />
          )}
          Search
        </button>
      </div>

      {/* Error */}
      {error && (
        <div className="mb-4 flex items-center gap-2 rounded-lg border border-red-200 bg-red-50 px-4 py-3 text-sm text-red-700">
          <AlertCircle className="h-4 w-4 shrink-0" />
          {error}
        </div>
      )}

      {/* Results */}
      {results.length > 0 && (
        <div className="overflow-hidden rounded-xl border border-gray-200 bg-white shadow-sm">
          <div className="border-b border-gray-100 px-4 py-3 text-sm text-gray-500">
            {results.length} result{results.length !== 1 ? "s" : ""}
          </div>
          <ul className="divide-y divide-gray-100">
            {results.map((r, i) => (
              <li
                key={r.info_hash ?? i}
                className="flex flex-col gap-2 px-4 py-3 hover:bg-gray-50 sm:flex-row sm:items-center"
              >
                {/* Title + badges */}
                <div className="flex-1 min-w-0">
                  <div className="flex flex-wrap items-center gap-1.5 mb-1">
                    <span
                      className={`rounded px-1.5 py-0.5 text-[10px] font-semibold uppercase ${sourceBadgeClass(r.source)}`}
                    >
                      {sourceLabel(r.source)}
                    </span>
                    {r.quality && r.quality !== "Unknown" && (
                      <span
                        className={`rounded px-1.5 py-0.5 text-[10px] font-semibold ${
                          QUALITY_COLORS[r.quality] ?? "bg-gray-100 text-gray-600"
                        }`}
                      >
                        {r.quality}
                      </span>
                    )}
                  </div>
                  <p className="text-sm font-medium text-gray-900 line-clamp-1">{r.title}</p>
                  <div className="mt-0.5 flex items-center gap-3 text-xs text-gray-500">
                    <span className="flex items-center gap-1">
                      <Users className="h-3 w-3" />
                      <span className="text-green-600 font-medium">{r.seeds}</span> seeds
                    </span>
                    <span>{r.size}</span>
                  </div>
                </div>

                {/* Actions */}
                <div className="flex shrink-0 items-center gap-2">
                  {r.url && (
                    <a
                      href={r.url}
                      target="_blank"
                      rel="noopener noreferrer"
                      className="rounded-lg p-1.5 text-gray-400 hover:bg-gray-100 hover:text-gray-600"
                      title="View on site"
                    >
                      <ExternalLink className="h-4 w-4" />
                    </a>
                  )}
                  <button
                    onClick={() => setWatchlistTarget(r)}
                    className="flex items-center gap-1.5 rounded-lg border border-gray-300 px-3 py-1.5 text-xs font-medium text-gray-600 hover:bg-gray-100"
                    title="Add to watchlist"
                  >
                    <Plus className="h-3.5 w-3.5" />
                    Watchlist
                  </button>
                  <button
                    onClick={() => setDownloadTarget(r)}
                    className="flex items-center gap-1.5 rounded-lg bg-blue-600 px-3 py-1.5 text-xs font-medium text-white hover:bg-blue-700"
                  >
                    <Download className="h-3.5 w-3.5" />
                    Download
                  </button>
                </div>
              </li>
            ))}
          </ul>
        </div>
      )}

      {!loading && results.length === 0 && query && !error && (
        <div className="py-16 text-center text-gray-400">
          <SearchIcon className="mx-auto mb-3 h-10 w-10 opacity-30" />
          <p>No results found. Try a different query or indexer.</p>
        </div>
      )}

      {!query && (
        <div className="py-16 text-center text-gray-400">
          <SearchIcon className="mx-auto mb-3 h-10 w-10 opacity-30" />
          <p className="text-sm">Type something above and press Search.</p>
        </div>
      )}

      {/* Modals */}
      {downloadTarget && (
        <DownloadModal
          result={downloadTarget}
          paths={paths}
          onClose={() => setDownloadTarget(null)}
          onConfirm={handleDownload}
        />
      )}
      {watchlistTarget && (
        <WatchlistModal
          result={watchlistTarget}
          paths={paths}
          onClose={() => setWatchlistTarget(null)}
          onConfirm={handleWatchlist}
        />
      )}

      {/* Toast */}
      {toast && (
        <div className="fixed bottom-6 right-6 z-50 rounded-lg bg-gray-900 px-4 py-3 text-sm text-white shadow-lg">
          {toast}
        </div>
      )}
    </div>
  );
}
