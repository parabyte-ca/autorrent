import { useState, useRef } from "react";
import {
  AlertCircle,
  Download,
  ExternalLink,
  Loader2,
  Plus,
  Search as SearchIcon,
  Shield,
  Users,
} from "lucide-react";
import { api, type DownloadPath, type TorrentResult, type WatchlistCreate } from "../api/client";

const QUALITIES = ["Any", "4K", "1080p", "720p", "480p"];
const CODECS    = ["x265", "x264", "AV1", "Any"];
const INDEXERS  = [
  { value: "all",     label: "All sources"    },
  { value: "nyaa",    label: "NYAA"           },
  { value: "tpb",     label: "The Pirate Bay" },
  { value: "jackett", label: "Jackett"        },
];

const SOURCE_COLORS: Record<string, string> = {
  nyaa:    "bg-purple-100 text-purple-700 dark:bg-purple-900/40 dark:text-purple-400",
  tpb:     "bg-orange-100 text-orange-700 dark:bg-orange-900/40 dark:text-orange-400",
  jackett: "bg-green-100  text-green-700  dark:bg-green-900/40  dark:text-green-400",
};
const QUALITY_COLORS: Record<string, string> = {
  "4K":     "bg-yellow-100 text-yellow-700 dark:bg-yellow-900/40 dark:text-yellow-400",
  "1080p":  "bg-blue-100   text-blue-700   dark:bg-blue-900/40   dark:text-blue-400",
  "720p":   "bg-sky-100    text-sky-700    dark:bg-sky-900/40    dark:text-sky-400",
  "480p":   "bg-gray-100   text-gray-700   dark:bg-gray-700      dark:text-gray-300",
};

const INPUT = [
  "rounded-lg border border-gray-300 px-3 py-2.5 text-sm",
  "focus:outline-none focus:ring-2 focus:ring-blue-500",
  "dark:bg-gray-800 dark:border-gray-600 dark:text-gray-100",
].join(" ");

function sourceLabel(src: string) {
  return src.startsWith("jackett/") ? src.replace("jackett/", "") : src.toUpperCase();
}
function sourceBadgeClass(src: string) {
  const k = src.startsWith("jackett") ? "jackett" : src;
  return SOURCE_COLORS[k] ?? "bg-gray-100 text-gray-600 dark:bg-gray-700 dark:text-gray-300";
}

// ── Download modal ────────────────────────────────────────────────────────────

function DownloadModal({
  result, paths, onClose, onConfirm,
}: {
  result: TorrentResult;
  paths: DownloadPath[];
  onClose: () => void;
  onConfirm: (pathId?: number) => void;
}) {
  const [pathId, setPathId] = useState<number | undefined>(paths.find((p) => p.is_default)?.id);

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/50 p-4">
      <div className="w-full max-w-md rounded-xl bg-white dark:bg-gray-900 p-6 shadow-xl border border-gray-200 dark:border-gray-700">
        <h2 className="mb-1 text-lg font-semibold text-gray-900 dark:text-gray-100">Download</h2>
        <p className="mb-4 text-sm text-gray-500 dark:text-gray-400 line-clamp-2">{result.title}</p>

        <label className="mb-1 block text-sm font-medium text-gray-700 dark:text-gray-300">Save to</label>
        {paths.length === 0 ? (
          <p className="mb-4 text-sm text-amber-600 dark:text-amber-400">
            No download paths configured. Add one in Settings first.
          </p>
        ) : (
          <select
            value={pathId ?? ""}
            onChange={(e) => setPathId(e.target.value ? Number(e.target.value) : undefined)}
            className={`mb-4 w-full ${INPUT}`}
          >
            {paths.map((p) => (
              <option key={p.id} value={p.id}>{p.name} — {p.path}</option>
            ))}
          </select>
        )}

        <div className="flex gap-3">
          <button onClick={onClose} className="flex-1 rounded-lg border border-gray-300 dark:border-gray-600 px-4 py-2 text-sm font-medium text-gray-700 dark:text-gray-300 hover:bg-gray-50 dark:hover:bg-gray-800">
            Cancel
          </button>
          <button onClick={() => onConfirm(pathId)} disabled={paths.length === 0} className="flex-1 rounded-lg bg-blue-600 px-4 py-2 text-sm font-medium text-white hover:bg-blue-700 disabled:opacity-50">
            Download
          </button>
        </div>
      </div>
    </div>
  );
}

// ── Watchlist modal ───────────────────────────────────────────────────────────

function WatchlistModal({
  result, paths, defaultCodec, onClose, onConfirm,
}: {
  result: TorrentResult;
  paths: DownloadPath[];
  defaultCodec: string;
  onClose: () => void;
  onConfirm: (data: WatchlistCreate) => void;
}) {
  const defaultPath = paths.find((p) => p.is_default);
  const [form, setForm] = useState<WatchlistCreate>({
    title:            result.title.replace(/S\d+E\d+.*/i, "").trim(),
    search_query:     result.title.replace(/S\d+E\d+.*/i, "").trim(),
    quality:          result.quality && result.quality !== "Unknown" ? result.quality : "1080p",
    codec:            defaultCodec !== "Any" ? defaultCodec : "x265",
    season:           1,
    episode:          1,
    download_path_id: defaultPath?.id,
    enabled:          true,
  });
  const set = (k: keyof WatchlistCreate, v: unknown) => setForm((f) => ({ ...f, [k]: v }));

  const FIELD = "w-full rounded-lg border border-gray-300 dark:border-gray-600 px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-blue-500 dark:bg-gray-800 dark:text-gray-100";

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/50 p-4">
      <div className="w-full max-w-md rounded-xl bg-white dark:bg-gray-900 p-6 shadow-xl border border-gray-200 dark:border-gray-700">
        <h2 className="mb-4 text-lg font-semibold text-gray-900 dark:text-gray-100">Add to Watchlist</h2>

        <div className="space-y-3">
          <div>
            <label className="mb-1 block text-sm font-medium text-gray-700 dark:text-gray-300">Show title</label>
            <input value={form.title} onChange={(e) => set("title", e.target.value)} className={FIELD} />
          </div>
          <div>
            <label className="mb-1 block text-sm font-medium text-gray-700 dark:text-gray-300">
              Search query <span className="text-xs font-normal text-gray-400">(what to search for)</span>
            </label>
            <input value={form.search_query} onChange={(e) => set("search_query", e.target.value)} className={FIELD} />
          </div>
          <div className="flex gap-3">
            <div className="flex-1">
              <label className="mb-1 block text-sm font-medium text-gray-700 dark:text-gray-300">Season</label>
              <input type="number" min={1} value={form.season} onChange={(e) => set("season", Number(e.target.value))} className={FIELD} />
            </div>
            <div className="flex-1">
              <label className="mb-1 block text-sm font-medium text-gray-700 dark:text-gray-300">Next episode</label>
              <input type="number" min={1} value={form.episode} onChange={(e) => set("episode", Number(e.target.value))} className={FIELD} />
            </div>
          </div>
          <div className="flex gap-3">
            <div className="flex-1">
              <label className="mb-1 block text-sm font-medium text-gray-700 dark:text-gray-300">Quality</label>
              <select value={form.quality} onChange={(e) => set("quality", e.target.value)} className={FIELD}>
                {QUALITIES.filter((q) => q !== "Any").map((q) => <option key={q}>{q}</option>)}
              </select>
            </div>
            <div className="flex-1">
              <label className="mb-1 block text-sm font-medium text-gray-700 dark:text-gray-300">Codec</label>
              <select value={form.codec} onChange={(e) => set("codec", e.target.value)} className={FIELD}>
                {CODECS.map((c) => <option key={c}>{c}</option>)}
              </select>
            </div>
          </div>
          {paths.length > 0 && (
            <div>
              <label className="mb-1 block text-sm font-medium text-gray-700 dark:text-gray-300">Save to</label>
              <select value={form.download_path_id ?? ""} onChange={(e) => set("download_path_id", e.target.value ? Number(e.target.value) : undefined)} className={FIELD}>
                <option value="">Use default</option>
                {paths.map((p) => <option key={p.id} value={p.id}>{p.name}</option>)}
              </select>
            </div>
          )}
        </div>

        <div className="mt-5 flex gap-3">
          <button onClick={onClose} className="flex-1 rounded-lg border border-gray-300 dark:border-gray-600 px-4 py-2 text-sm font-medium text-gray-700 dark:text-gray-300 hover:bg-gray-50 dark:hover:bg-gray-800">
            Cancel
          </button>
          <button onClick={() => onConfirm(form)} className="flex-1 rounded-lg bg-blue-600 px-4 py-2 text-sm font-medium text-white hover:bg-blue-700">
            Add to Watchlist
          </button>
        </div>
      </div>
    </div>
  );
}

// ── Main page ─────────────────────────────────────────────────────────────────

export default function Search() {
  const [query,       setQuery]       = useState("");
  const [quality,     setQuality]     = useState("Any");
  const [codec,       setCodec]       = useState("x265");
  const [indexer,     setIndexer]     = useState("all");
  const [filterAdult, setFilterAdult] = useState(true);
  const [results,     setResults]     = useState<TorrentResult[]>([]);
  const [loading,     setLoading]     = useState(false);
  const [error,       setError]       = useState<string | null>(null);
  const [paths,       setPaths]       = useState<DownloadPath[]>([]);
  const [dlTarget,    setDlTarget]    = useState<TorrentResult | null>(null);
  const [wlTarget,    setWlTarget]    = useState<TorrentResult | null>(null);
  const [toast,       setToast]       = useState<string | null>(null);
  const inputRef = useRef<HTMLInputElement>(null);

  const showToast = (msg: string) => { setToast(msg); setTimeout(() => setToast(null), 3000); };

  const doSearch = async () => {
    if (!query.trim()) return;
    setLoading(true);
    setError(null);
    try {
      const [res, p] = await Promise.all([
        api.search(query, indexer, quality, codec, filterAdult),
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
    if (!dlTarget) return;
    try {
      await api.downloads.add({ magnet: dlTarget.magnet, title: dlTarget.title, download_path_id: pathId });
      showToast("Added to qBittorrent!");
    } catch (e: unknown) {
      showToast(`Error: ${(e as Error).message}`);
    }
    setDlTarget(null);
  };

  const handleWatchlist = async (data: WatchlistCreate) => {
    try {
      await api.watchlist.create(data);
      showToast(`"${data.title}" added to watchlist!`);
    } catch (e: unknown) {
      showToast(`Error: ${(e as Error).message}`);
    }
    setWlTarget(null);
  };

  const CTRL = "rounded-lg border border-gray-300 dark:border-gray-600 px-2.5 py-1.5 text-sm dark:bg-gray-800 dark:text-gray-100 focus:outline-none focus:ring-2 focus:ring-blue-500";

  return (
    <div className="px-4 py-6 sm:px-6">
      <div className="mb-6">
        <h1 className="text-2xl font-bold text-gray-900 dark:text-gray-100">Search Torrents</h1>
        <p className="mt-1 text-sm text-gray-500 dark:text-gray-400">Find torrents and send them straight to qBittorrent.</p>
      </div>

      {/* Search bar */}
      <div className="mb-3 flex flex-col gap-3 sm:flex-row">
        <input
          ref={inputRef}
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          onKeyDown={(e) => e.key === "Enter" && doSearch()}
          placeholder="Search for a show, movie, or episode…"
          className="flex-1 rounded-lg border border-gray-300 dark:border-gray-600 px-4 py-2.5 text-sm shadow-sm focus:outline-none focus:ring-2 focus:ring-blue-500 dark:bg-gray-800 dark:text-gray-100 dark:placeholder-gray-500"
        />
        <button
          onClick={doSearch}
          disabled={loading}
          className="flex items-center gap-2 rounded-lg bg-blue-600 px-5 py-2.5 text-sm font-medium text-white hover:bg-blue-700 disabled:opacity-60"
        >
          {loading ? <Loader2 className="h-4 w-4 animate-spin" /> : <SearchIcon className="h-4 w-4" />}
          Search
        </button>
      </div>

      {/* Filters row */}
      <div className="mb-4 flex flex-wrap items-center gap-3">
        <div className="flex items-center gap-1.5">
          <label className="text-xs font-medium text-gray-500 dark:text-gray-400">Quality</label>
          <select value={quality} onChange={(e) => setQuality(e.target.value)} className={CTRL}>
            {QUALITIES.map((q) => <option key={q}>{q}</option>)}
          </select>
        </div>
        <div className="flex items-center gap-1.5">
          <label className="text-xs font-medium text-gray-500 dark:text-gray-400">Codec</label>
          <select value={codec} onChange={(e) => setCodec(e.target.value)} className={CTRL}>
            {CODECS.map((c) => <option key={c}>{c}</option>)}
          </select>
        </div>
        <div className="flex items-center gap-1.5">
          <label className="text-xs font-medium text-gray-500 dark:text-gray-400">Source</label>
          <select value={indexer} onChange={(e) => setIndexer(e.target.value)} className={CTRL}>
            {INDEXERS.map((i) => <option key={i.value} value={i.value}>{i.label}</option>)}
          </select>
        </div>

        <button
          onClick={() => setFilterAdult((v) => !v)}
          title={filterAdult ? "Adult content is filtered — click to disable" : "Adult content is visible — click to filter"}
          className={`ml-auto flex items-center gap-1.5 rounded-lg border px-3 py-1.5 text-xs font-medium transition-colors ${
            filterAdult
              ? "border-green-300 bg-green-50 text-green-700 hover:bg-green-100 dark:border-green-800 dark:bg-green-950 dark:text-green-400"
              : "border-red-300 bg-red-50 text-red-600 hover:bg-red-100 dark:border-red-800 dark:bg-red-950 dark:text-red-400"
          }`}
        >
          <Shield className="h-3.5 w-3.5" />
          {filterAdult ? "Adult filter: ON" : "Adult filter: OFF"}
        </button>
      </div>

      {/* Error */}
      {error && (
        <div className="mb-4 flex items-center gap-2 rounded-lg border border-red-200 dark:border-red-900 bg-red-50 dark:bg-red-950 px-4 py-3 text-sm text-red-700 dark:text-red-400">
          <AlertCircle className="h-4 w-4 shrink-0" />
          {error}
        </div>
      )}

      {/* Results */}
      {results.length > 0 && (
        <div className="overflow-hidden rounded-xl border border-gray-200 dark:border-gray-700 bg-white dark:bg-gray-900 shadow-sm">
          <div className="border-b border-gray-100 dark:border-gray-800 px-4 py-3 text-sm text-gray-500 dark:text-gray-400">
            {results.length} result{results.length !== 1 ? "s" : ""}
          </div>
          <ul className="divide-y divide-gray-100 dark:divide-gray-800">
            {results.map((r, i) => (
              <li key={r.info_hash ?? i} className="flex flex-col gap-2 px-4 py-3 hover:bg-gray-50 dark:hover:bg-gray-800 sm:flex-row sm:items-center">
                <div className="flex-1 min-w-0">
                  <div className="flex flex-wrap items-center gap-1.5 mb-1">
                    <span className={`rounded px-1.5 py-0.5 text-[10px] font-semibold uppercase ${sourceBadgeClass(r.source)}`}>
                      {sourceLabel(r.source)}
                    </span>
                    {r.quality && r.quality !== "Unknown" && (
                      <span className={`rounded px-1.5 py-0.5 text-[10px] font-semibold ${QUALITY_COLORS[r.quality] ?? "bg-gray-100 text-gray-600 dark:bg-gray-700 dark:text-gray-300"}`}>
                        {r.quality}
                      </span>
                    )}
                  </div>
                  <p className="text-sm font-medium text-gray-900 dark:text-gray-100 line-clamp-1">{r.title}</p>
                  <div className="mt-0.5 flex items-center gap-3 text-xs text-gray-500 dark:text-gray-400">
                    <span className="flex items-center gap-1">
                      <Users className="h-3 w-3" />
                      <span className="text-green-600 dark:text-green-400 font-medium">{r.seeds}</span> seeds
                    </span>
                    <span>{r.size}</span>
                  </div>
                </div>
                <div className="flex shrink-0 items-center gap-2">
                  {r.url && (
                    <a href={r.url} target="_blank" rel="noopener noreferrer"
                      className="flex min-h-[44px] min-w-[44px] items-center justify-center rounded-lg text-gray-400 hover:bg-gray-100 dark:hover:bg-gray-700 hover:text-gray-600 dark:hover:text-gray-300"
                      title="View on site">
                      <ExternalLink className="h-4 w-4" />
                    </a>
                  )}
                  <button onClick={() => setWlTarget(r)}
                    className="flex min-h-[44px] items-center gap-1.5 rounded-lg border border-gray-300 dark:border-gray-600 px-3 py-1.5 text-xs font-medium text-gray-600 dark:text-gray-300 hover:bg-gray-100 dark:hover:bg-gray-700">
                    <Plus className="h-3.5 w-3.5" />
                    Watchlist
                  </button>
                  <button onClick={() => setDlTarget(r)}
                    className="flex min-h-[44px] items-center gap-1.5 rounded-lg bg-blue-600 px-3 py-1.5 text-xs font-medium text-white hover:bg-blue-700">
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
        <div className="py-16 text-center text-gray-400 dark:text-gray-600">
          <SearchIcon className="mx-auto mb-3 h-10 w-10 opacity-30" />
          <p>No results found. Try a different query or indexer.</p>
        </div>
      )}
      {!query && (
        <div className="py-16 text-center text-gray-400 dark:text-gray-600">
          <SearchIcon className="mx-auto mb-3 h-10 w-10 opacity-30" />
          <p className="text-sm">Type something above and press Search.</p>
        </div>
      )}

      {dlTarget && (
        <DownloadModal result={dlTarget} paths={paths} onClose={() => setDlTarget(null)} onConfirm={handleDownload} />
      )}
      {wlTarget && (
        <WatchlistModal result={wlTarget} paths={paths} defaultCodec={codec} onClose={() => setWlTarget(null)} onConfirm={handleWatchlist} />
      )}

      {toast && (
        <div className="fixed bottom-20 right-4 z-50 rounded-lg bg-gray-900 dark:bg-gray-700 px-4 py-3 text-sm text-white shadow-lg md:bottom-6 md:right-6">
          {toast}
        </div>
      )}
    </div>
  );
}
