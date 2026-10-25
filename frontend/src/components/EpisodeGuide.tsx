import { useCallback, useEffect, useState } from "react";
import { CheckCircle2, ChevronDown, Loader2, Search } from "lucide-react";
import { api, type EpisodeGuide as Guide, type GuideEpisode } from "../api/client";

const CHIP: Record<GuideEpisode["status"], string> = {
  downloaded: "bg-green-100 dark:bg-green-900/60 text-green-700 dark:text-green-300 border-green-200 dark:border-green-700/60",
  missing: "bg-amber-100 dark:bg-amber-900/50 text-amber-800 dark:text-amber-300 border-amber-300 dark:border-amber-700 hover:bg-amber-200 dark:hover:bg-amber-900",
  upcoming: "bg-gray-50 dark:bg-gray-800 text-gray-400 dark:text-gray-500 border-gray-200 dark:border-gray-700",
};

function chipTitle(e: GuideEpisode): string {
  const when = e.airs_at ? new Date(e.airs_at).toLocaleString() : "air date unknown";
  const lines = [e.name || `Episode ${e.number}`, when];
  if (e.status === "downloaded" && e.torrent_name) lines.push(e.torrent_name);
  if (e.status === "missing") lines.push("Aired but not downloaded — click to search now");
  return lines.join("\n");
}

/** Per-show episode guide: what you have, what you're missing, what's still to air. */
export default function EpisodeGuide({ watchlistId, onChanged }: {
  watchlistId: number;
  onChanged?: () => void;
}) {
  const [guide, setGuide] = useState<Guide | null>(null);
  const [error, setError] = useState(false);
  const [open, setOpen] = useState<Set<number>>(new Set());
  const [busy, setBusy] = useState<string | null>(null);
  const [note, setNote] = useState<{ ok: boolean; text: string } | null>(null);

  const load = useCallback(async () => {
    try {
      const g = await api.watchlist.guide(watchlistId);
      setGuide(g);
      setError(false);
      setOpen((prev) => {
        if (prev.size || !g.seasons.length) return prev;
        return new Set([g.seasons[g.seasons.length - 1].season]);   // newest season first
      });
    } catch {
      setError(true);
    }
  }, [watchlistId]);

  useEffect(() => { load(); }, [load]);

  const search = async (season: number, e: GuideEpisode) => {
    const key = `${season}-${e.number}`;
    setBusy(key);
    setNote(null);
    try {
      const r = await api.watchlist.searchEpisode(watchlistId, season, e.number);
      setNote({ ok: r.status === "started", text: r.message });
      if (r.status === "started" || r.status === "already_downloaded") {
        await load();
        onChanged?.();
      }
    } catch (err) {
      setNote({ ok: false, text: err instanceof Error ? err.message : "Search failed." });
    } finally {
      setBusy(null);
    }
  };

  if (error) {
    return <p className="py-1 text-xs text-gray-400 dark:text-gray-600">Could not load the episode guide.</p>;
  }
  if (!guide) {
    return (
      <div className="flex items-center gap-1.5 py-1 text-xs text-gray-400">
        <Loader2 className="h-3.5 w-3.5 animate-spin" /> Loading guide…
      </div>
    );
  }
  if (!guide.available) {
    return (
      <p className="py-1 text-xs text-gray-400 dark:text-gray-600">
        No episode data — TVMaze doesn't know this show under its search query.
      </p>
    );
  }

  return (
    <div className="space-y-2">
      <p className="text-xs text-gray-400 dark:text-gray-500">
        Looking next for S{String(guide.pointer.season).padStart(2, "0")}E{String(guide.pointer.episode).padStart(2, "0")}.
        Amber episodes have aired but are missing — click one to search for it now.
      </p>
      {note && (
        <p className={`rounded-lg px-2.5 py-1.5 text-xs ${note.ok
          ? "bg-green-50 dark:bg-green-950/40 text-green-700 dark:text-green-300"
          : "bg-amber-50 dark:bg-amber-950/40 text-amber-800 dark:text-amber-300"}`}>
          {note.text}
        </p>
      )}
      {[...guide.seasons].reverse().map((s) => {
        const expanded = open.has(s.season);
        const complete = s.aired > 0 && s.downloaded >= s.aired;
        return (
          <div key={s.season}>
            <button
              onClick={() => setOpen((prev) => {
                const next = new Set(prev);
                if (next.has(s.season)) next.delete(s.season); else next.add(s.season);
                return next;
              })}
              className="flex w-full items-center gap-1.5 text-left text-xs font-medium text-gray-500 dark:text-gray-400 hover:text-gray-700 dark:hover:text-gray-200"
            >
              <ChevronDown className={`h-3 w-3 transition-transform ${expanded ? "" : "-rotate-90"}`} />
              Season {s.season}
              <span className="font-normal text-gray-400 dark:text-gray-500">
                {s.downloaded}/{s.aired} aired{s.total > s.aired ? ` · ${s.total - s.aired} to come` : ""}
              </span>
              {complete && <CheckCircle2 className="h-3 w-3 text-green-500" />}
            </button>
            {expanded && (
              <div className="mt-1 flex flex-wrap gap-1">
                {s.episodes.map((e) => {
                  const key = `${s.season}-${e.number}`;
                  const clickable = e.status === "missing" && busy === null;
                  return (
                    <button
                      key={e.number}
                      disabled={!clickable}
                      onClick={() => search(s.season, e)}
                      title={chipTitle(e)}
                      className={`inline-flex min-w-[2rem] items-center justify-center gap-1 rounded border px-1.5 py-0.5 text-xs font-medium transition-colors ${CHIP[e.status]} ${clickable ? "cursor-pointer" : "cursor-default"}`}
                    >
                      {busy === key ? <Loader2 className="h-3 w-3 animate-spin" /> : e.status === "missing" ? <Search className="h-3 w-3" /> : null}
                      {e.number}
                    </button>
                  );
                })}
              </div>
            )}
          </div>
        );
      })}
    </div>
  );
}
