import { useEffect, useRef, useState } from "react";
import { Link } from "react-router-dom";
import { CheckCircle2, Loader2, Plus, Search, Star, X } from "lucide-react";
import { api, type BrowseShow } from "../api/client";
import Poster from "../components/Poster";
import { fmtDate } from "../utils";

function AddDialog({ show, onClose, onAdded }: {
  show: BrowseShow;
  onClose: () => void;
  onAdded: () => void;
}) {
  const running = show.status === "Running";
  const [start, setStart] = useState<"next" | "beginning">(running ? "next" : "beginning");
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const add = async () => {
    setSaving(true);
    setError(null);
    try {
      await api.watchlist.addFromTvmaze({ tvmaze_id: show.tvmaze_id, start });
      onAdded();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not add the show.");
      setSaving(false);
    }
  };

  const option = (value: "next" | "beginning", title: string, hint: string) => (
    <label className={`flex cursor-pointer items-start gap-3 rounded-lg border p-3 ${start === value
      ? "border-blue-500 bg-blue-50 dark:bg-blue-950/30"
      : "border-gray-200 dark:border-gray-700"}`}>
      <input type="radio" name="start" checked={start === value} onChange={() => setStart(value)} className="mt-0.5" />
      <div>
        <span className="text-sm font-medium text-gray-900 dark:text-gray-100">{title}</span>
        <p className="text-xs text-gray-500 dark:text-gray-400">{hint}</p>
      </div>
    </label>
  );

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/50 p-4">
      <div className="w-full max-w-md rounded-xl border border-gray-200 dark:border-gray-700 bg-white dark:bg-gray-900 p-6 shadow-xl">
        <div className="mb-4 flex gap-3">
          <Poster url={show.poster_url} title={show.name} className="h-24 w-16" />
          <div className="min-w-0">
            <h2 className="text-lg font-semibold text-gray-900 dark:text-gray-100">{show.name}</h2>
            <p className="text-xs text-gray-500 dark:text-gray-400">
              {[show.network, show.status].filter(Boolean).join(" · ")}
            </p>
          </div>
        </div>
        <p className="mb-2 text-sm font-medium text-gray-700 dark:text-gray-300">Where should AutoRrent start?</p>
        <div className="space-y-2">
          {option("next", "Next new episode", "Only download episodes from now on.")}
          {option("beginning", "From the beginning", "Download every episode, starting with season 1.")}
        </div>
        <p className="mt-3 text-xs text-gray-400 dark:text-gray-500">
          Uses your default quality profile if you've set one — change anything later from the Watchlist.
        </p>
        {error && <p className="mt-3 text-sm text-red-600 dark:text-red-400">{error}</p>}
        <div className="mt-5 flex gap-3">
          <button onClick={onClose} className="flex-1 rounded-lg border border-gray-300 dark:border-gray-600 px-4 py-2 text-sm font-medium text-gray-700 dark:text-gray-300 hover:bg-gray-50 dark:hover:bg-gray-800">
            Cancel
          </button>
          <button onClick={add} disabled={saving} className="flex-1 rounded-lg bg-blue-600 px-4 py-2 text-sm font-medium text-white hover:bg-blue-700 disabled:opacity-50">
            {saving ? "Adding…" : "Add to watchlist"}
          </button>
        </div>
      </div>
    </div>
  );
}

export default function Browse() {
  const [input, setInput] = useState("");
  const [query, setQuery] = useState("");
  const [shows, setShows] = useState<BrowseShow[] | null>(null);
  const [mode, setMode] = useState<"airing" | "search">("airing");
  const [country, setCountry] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [target, setTarget] = useState<BrowseShow | null>(null);
  const [toast, setToast] = useState<string | null>(null);
  const reqId = useRef(0);

  // Debounce the search box.
  useEffect(() => {
    const t = setTimeout(() => setQuery(input.trim()), 400);
    return () => clearTimeout(t);
  }, [input]);

  const load = async (q: string) => {
    const id = ++reqId.current;
    setShows(null);
    setError(null);
    try {
      const r = await api.browse.get(q);
      if (id !== reqId.current) return;   // a newer request superseded this one
      setShows(r.shows);
      setMode(r.mode);
      setCountry(r.country);
    } catch (err) {
      if (id !== reqId.current) return;
      setError(err instanceof Error ? err.message : "Could not load shows.");
      setShows([]);
    }
  };

  useEffect(() => { load(query); }, [query]);

  const added = () => {
    const name = target?.name;
    setTarget(null);
    setToast(`Added ${name}`);
    setTimeout(() => setToast(null), 3000);
    load(query);
  };

  return (
    <div className="px-4 py-6 sm:px-6">
      <div className="mb-5">
        <h1 className="text-2xl font-bold text-gray-900 dark:text-gray-100">Browse shows</h1>
        <p className="mt-1 text-sm text-gray-500 dark:text-gray-400">
          {mode === "airing" && !query
            ? `Popular shows airing in the next week${country ? ` (${country})` : ""} — add one to start tracking it.`
            : "Search results from TVMaze."}
        </p>
      </div>

      <div className="relative mb-5 max-w-xl">
        <Search className="pointer-events-none absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-gray-400" />
        <input
          value={input}
          onChange={(e) => setInput(e.target.value)}
          placeholder="Search for any show…"
          className="w-full rounded-lg border border-gray-300 dark:border-gray-600 py-2.5 pl-9 pr-9 text-sm focus:outline-none focus:ring-2 focus:ring-blue-500 dark:bg-gray-800 dark:text-gray-100"
        />
        {input && (
          <button onClick={() => setInput("")} tabIndex={-1} aria-label="Clear search"
            className="absolute right-2 top-1/2 -translate-y-1/2 rounded p-1 text-gray-400 hover:text-gray-600">
            <X className="h-4 w-4" />
          </button>
        )}
      </div>

      {error && <p className="mb-4 text-sm text-red-600 dark:text-red-400">{error}</p>}
      {shows === null && !error && (
        <div className="flex justify-center py-16"><Loader2 className="h-6 w-6 animate-spin text-blue-600" /></div>
      )}
      {shows !== null && shows.length === 0 && !error && (
        <p className="py-12 text-center text-sm text-gray-400 dark:text-gray-600">
          {query.length === 1 ? "Keep typing…" : query ? `No shows found for "${query}".` : "Nothing is airing right now."}
        </p>
      )}

      <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-3">
        {(shows ?? []).map((s) => (
          <div key={s.tvmaze_id} className="flex gap-3 rounded-xl border border-gray-200 dark:border-gray-700 bg-white dark:bg-gray-900 p-3 shadow-sm">
            <Poster url={s.poster_url} title={s.name} className="h-32 w-[5.5rem]" />
            <div className="flex min-w-0 flex-1 flex-col">
              <div className="flex items-start justify-between gap-2">
                <h3 className="font-semibold leading-tight text-gray-900 dark:text-gray-100">{s.name}</h3>
                {s.rating != null && (
                  <span className="flex shrink-0 items-center gap-0.5 text-xs text-gray-500 dark:text-gray-400">
                    <Star className="h-3 w-3 fill-yellow-400 text-yellow-400" />{s.rating.toFixed(1)}
                  </span>
                )}
              </div>
              <p className="mt-0.5 text-xs text-gray-500 dark:text-gray-400">
                {[s.network, s.premiered?.slice(0, 4), s.genres.slice(0, 2).join(", ")].filter(Boolean).join(" · ")}
              </p>
              {s.next_airs && (
                <p className="mt-1 flex flex-wrap items-center gap-1.5 text-xs text-gray-600 dark:text-gray-300">
                  {s.season_premiere && (
                    <span className="rounded bg-blue-100 dark:bg-blue-900/60 px-1.5 py-0.5 font-medium text-blue-700 dark:text-blue-300">Premiere</span>
                  )}
                  {s.next_episode} · {fmtDate(s.next_airs)}
                </p>
              )}
              {s.summary && <p className="mt-1 line-clamp-2 text-xs text-gray-400 dark:text-gray-500">{s.summary}</p>}
              <div className="mt-auto pt-2">
                {s.on_watchlist ? (
                  <Link to="/watchlist" className="inline-flex items-center gap-1 text-xs font-medium text-green-600 dark:text-green-400">
                    <CheckCircle2 className="h-3.5 w-3.5" /> On your watchlist
                  </Link>
                ) : (
                  <button onClick={() => setTarget(s)}
                    className="inline-flex min-h-[36px] items-center gap-1 rounded-lg bg-blue-600 px-3 text-xs font-medium text-white hover:bg-blue-700">
                    <Plus className="h-3.5 w-3.5" /> Add
                  </button>
                )}
              </div>
            </div>
          </div>
        ))}
      </div>

      {target && <AddDialog show={target} onClose={() => setTarget(null)} onAdded={added} />}
      {toast && (
        <div className="fixed bottom-20 right-4 z-50 rounded-lg bg-gray-900 dark:bg-gray-700 px-4 py-3 text-sm text-white shadow-lg md:bottom-6 md:right-6">
          {toast}
        </div>
      )}
    </div>
  );
}
