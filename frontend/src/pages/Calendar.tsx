import { useCallback, useEffect, useMemo, useState } from "react";
import { Link } from "react-router-dom";
import { CalendarDays, CheckCircle2, Clock, Loader2, Search } from "lucide-react";
import { api, type CalendarEntry } from "../api/client";
import Poster from "../components/Poster";
import { fmtDayHeading, fmtTime } from "../utils";

const STATUS: Record<CalendarEntry["status"], { label: string; cls: string }> = {
  downloaded: { label: "Downloaded", cls: "bg-green-100 dark:bg-green-900/50 text-green-700 dark:text-green-300" },
  missing: { label: "Not downloaded", cls: "bg-amber-100 dark:bg-amber-900/50 text-amber-800 dark:text-amber-300" },
  upcoming: { label: "Upcoming", cls: "bg-gray-100 dark:bg-gray-800 text-gray-500 dark:text-gray-400" },
};

const epLabel = (e: CalendarEntry) =>
  `S${String(e.season).padStart(2, "0")}E${String(e.episode).padStart(2, "0")}`;

export default function Calendar() {
  const [entries, setEntries] = useState<CalendarEntry[] | null>(null);
  const [unmatched, setUnmatched] = useState(0);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [note, setNote] = useState<{ ok: boolean; text: string } | null>(null);

  const load = useCallback(async () => {
    try {
      const r = await api.calendar.get(30, 7);
      setEntries(r.entries);
      setUnmatched(r.unmatched);
      setError(null);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not load the calendar.");
    }
  }, []);

  useEffect(() => { load(); }, [load]);

  const groups = useMemo(() => {
    const byDay = new Map<string, CalendarEntry[]>();
    for (const e of entries ?? []) {
      const d = new Date(e.airs_at);
      const key = `${d.getFullYear()}-${d.getMonth()}-${d.getDate()}`;
      (byDay.get(key) ?? byDay.set(key, []).get(key)!).push(e);
    }
    return [...byDay.values()].map((list) => ({ day: new Date(list[0].airs_at), list }));
  }, [entries]);

  const search = async (e: CalendarEntry) => {
    const key = `${e.watchlist_id}-${e.season}-${e.episode}`;
    setBusy(key);
    setNote(null);
    try {
      const r = await api.watchlist.searchEpisode(e.watchlist_id, e.season, e.episode);
      setNote({ ok: r.status === "started", text: r.message });
      if (r.status === "started" || r.status === "already_downloaded") await load();
    } catch (err) {
      setNote({ ok: false, text: err instanceof Error ? err.message : "Search failed." });
    } finally {
      setBusy(null);
    }
  };

  const today = fmtDayHeading(new Date());

  return (
    <div className="px-4 py-6 sm:px-6">
      <div className="mb-5">
        <h1 className="text-2xl font-bold text-gray-900 dark:text-gray-100">Calendar</h1>
        <p className="mt-1 text-sm text-gray-500 dark:text-gray-400">
          Episodes of your watchlist shows from the past week to the next 30 days.
        </p>
      </div>

      {note && (
        <p className={`mb-4 rounded-lg px-3 py-2 text-sm ${note.ok
          ? "bg-green-50 dark:bg-green-950/40 text-green-700 dark:text-green-300"
          : "bg-amber-50 dark:bg-amber-950/40 text-amber-800 dark:text-amber-300"}`}>
          {note.text}
        </p>
      )}

      {error && <p className="text-sm text-red-600 dark:text-red-400">{error}</p>}
      {!error && entries === null && (
        <div className="flex justify-center py-16"><Loader2 className="h-6 w-6 animate-spin text-blue-600" /></div>
      )}

      {entries !== null && entries.length === 0 && (
        <div className="py-16 text-center text-gray-400 dark:text-gray-600">
          <CalendarDays className="mx-auto mb-3 h-12 w-12 opacity-30" />
          <p className="font-medium">Nothing scheduled.</p>
          <p className="mt-1 text-sm">
            Add shows from the <Link to="/watchlist" className="text-blue-600 dark:text-blue-400 underline">Watchlist</Link> or{" "}
            <Link to="/browse" className="text-blue-600 dark:text-blue-400 underline">Browse</Link> to see their air dates here.
          </p>
        </div>
      )}

      <div className="space-y-6">
        {groups.map(({ day, list }) => {
          const heading = fmtDayHeading(day);
          return (
            <section key={day.toDateString()}>
              <h2 className={`mb-2 text-sm font-semibold uppercase tracking-wide ${heading === today
                ? "text-blue-600 dark:text-blue-400" : "text-gray-500 dark:text-gray-400"}`}>
                {heading}
                {["Today", "Tomorrow", "Yesterday"].includes(heading) && (
                  <span className="ml-2 text-xs font-normal normal-case text-gray-400 dark:text-gray-500">
                    {day.toLocaleDateString(undefined, { month: "short", day: "numeric" })}
                  </span>
                )}
              </h2>
              <ul className="divide-y divide-gray-100 dark:divide-gray-800 overflow-hidden rounded-xl border border-gray-200 dark:border-gray-700 bg-white dark:bg-gray-900 shadow-sm">
                {list.map((e) => {
                  const key = `${e.watchlist_id}-${e.season}-${e.episode}`;
                  const st = STATUS[e.status];
                  return (
                    <li key={key} className={`flex items-center gap-3 px-3 py-2.5 ${e.enabled ? "" : "opacity-60"}`}>
                      <Poster url={e.poster_url} title={e.title} className="h-14 w-10" />
                      <div className="min-w-0 flex-1">
                        <p className="truncate text-sm font-medium text-gray-900 dark:text-gray-100">{e.title}</p>
                        <p className="truncate text-xs text-gray-500 dark:text-gray-400">
                          {epLabel(e)}{e.name ? ` · ${e.name}` : ""}
                        </p>
                        <p className="mt-0.5 flex items-center gap-1 text-xs text-gray-400 dark:text-gray-500">
                          <Clock className="h-3 w-3" /> {fmtTime(e.airs_at)}
                          {!e.enabled && <span className="ml-1">· paused</span>}
                        </p>
                      </div>
                      <span className={`hidden shrink-0 items-center gap-1 rounded-full px-2 py-0.5 text-xs font-medium sm:inline-flex ${st.cls}`}>
                        {e.status === "downloaded" && <CheckCircle2 className="h-3 w-3" />}
                        {st.label}
                      </span>
                      {e.status === "missing" ? (
                        <button
                          onClick={() => search(e)}
                          disabled={busy !== null}
                          className="flex min-h-[44px] shrink-0 items-center gap-1.5 rounded-lg border border-gray-300 dark:border-gray-600 px-3 text-xs font-medium text-gray-700 dark:text-gray-300 hover:bg-gray-100 dark:hover:bg-gray-800 disabled:opacity-50"
                        >
                          {busy === key ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <Search className="h-3.5 w-3.5" />}
                          Search
                        </button>
                      ) : (
                        <span className={`shrink-0 sm:hidden rounded-full px-2 py-0.5 text-[11px] font-medium ${st.cls}`}>{st.label}</span>
                      )}
                    </li>
                  );
                })}
              </ul>
            </section>
          );
        })}
      </div>

      {unmatched > 0 && (
        <p className="mt-6 text-xs text-gray-400 dark:text-gray-500">
          {unmatched} show{unmatched === 1 ? "" : "s"} couldn't be matched to TVMaze, so their air dates aren't shown.
        </p>
      )}
    </div>
  );
}
