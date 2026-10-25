import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import {
  AlertTriangle,
  CheckCircle2,
  ChevronDown,
  Clock,
  Edit2,
  Loader2,
  Play,
  Plus,
  RefreshCw,
  Search,
  Trash2,
  Tv,
  XCircle,
} from "lucide-react";
import {
  api,
  type DownloadPath,
  type MarkEpisodeRequest,
  type QualityProfile,
  type WatchlistCreate,
  type WatchlistEpisode,
  type WatchlistItem,
} from "../api/client";
import EpisodeGuide from "../components/EpisodeGuide";
import Poster from "../components/Poster";
import { fmtAgo, fmtDate } from "../utils";

const QUALITIES = ["4K", "1080p", "720p", "480p", "Any"];
const CODECS    = ["x265", "x264", "AV1", "Any"];

function fmtFree(bytes?: number | null): string | null {
  if (bytes == null) return null;
  if (bytes >= 1e12) return `${(bytes / 1e12).toFixed(1)} TB free`;
  if (bytes >= 1e9)  return `${(bytes / 1e9).toFixed(1)} GB free`;
  return `${(bytes / 1e6).toFixed(0)} MB free`;
}

const FIELD = [
  "w-full rounded-lg border border-gray-300 dark:border-gray-600 px-3 py-2 text-sm",
  "focus:outline-none focus:ring-2 focus:ring-blue-500",
  "dark:bg-gray-800 dark:text-gray-100",
].join(" ");

function epLabel(s: number, e: number) {
  return `S${String(s).padStart(2, "0")}E${String(e).padStart(2, "0")}`;
}

function ShowStatusBadge({ status }: { status: WatchlistItem["show_status"] }) {
  if (!status) return null;
  const cfg: Record<string, { cls: string; label: string }> = {
    Running:           { cls: "bg-green-100 dark:bg-green-900/60 text-green-700 dark:text-green-300 border-green-200 dark:border-green-700/60", label: "Running" },
    Ended:             { cls: "bg-red-100 dark:bg-red-900/60 text-red-700 dark:text-red-300 border-red-200 dark:border-red-700/60", label: "Ended" },
    "To Be Determined":{ cls: "bg-yellow-100 dark:bg-yellow-900/60 text-yellow-700 dark:text-yellow-300 border-yellow-200 dark:border-yellow-700/60", label: "TBD" },
    "In Development":  { cls: "bg-blue-100 dark:bg-blue-900/60 text-blue-700 dark:text-blue-300 border-blue-200 dark:border-blue-700/60", label: "In Dev" },
    Unknown:           { cls: "bg-gray-100 dark:bg-gray-800 text-gray-500 dark:text-gray-400 border-gray-200 dark:border-gray-700", label: "Unknown" },
  };
  const { cls, label } = cfg[status] ?? cfg["Unknown"];
  return (
    <span className={`inline-flex items-center rounded px-1.5 py-0.5 text-xs font-medium border ${cls}`}>
      {label}
    </span>
  );
}

// ── Form modal ────────────────────────────────────────────────────────────────

function profileSummary(p: QualityProfile): string {
  const parts = [p.quality, p.codec];
  if (p.max_size_mb) parts.push(`max ${p.max_size_mb >= 1024 ? `${(p.max_size_mb / 1024).toFixed(1)} GB` : `${p.max_size_mb} MB`}`);
  if (p.preferred_groups) parts.push(`groups: ${p.preferred_groups}`);
  if (p.prefer_season_packs) parts.push("season packs");
  if (p.fallback_quality) parts.push(`falls back to ${p.fallback_quality} after ${p.fallback_after_hours}h`);
  return parts.join(" · ");
}

function FormModal({ initial, paths, profiles, defaultProfileId, onClose, onSave }: {
  initial?: WatchlistItem;
  paths: DownloadPath[];
  profiles: QualityProfile[];
  defaultProfileId: number | null;
  onClose: () => void;
  onSave: (data: WatchlistCreate) => void;
}) {
  const defaultPath = paths.find((p) => p.is_default);
  const [form, setForm] = useState<WatchlistCreate>(
    initial
      ? { title: initial.title, search_query: initial.search_query, quality: initial.quality, codec: initial.codec ?? "x265", season: initial.season, episode: initial.episode, download_path_id: initial.download_path_id, enabled: initial.enabled, max_size_mb: initial.max_size_mb ?? null, preferred_groups: initial.preferred_groups ?? null, prefer_season_packs: initial.prefer_season_packs ?? false, profile_id: initial.profile_id ?? null }
      : { title: "", search_query: "", quality: "1080p", codec: "x265", season: 1, episode: 1, download_path_id: defaultPath?.id, enabled: true, max_size_mb: null, preferred_groups: null, prefer_season_packs: false, profile_id: profiles.some((p) => p.id === defaultProfileId) ? defaultProfileId : null }
  );
  const profile = profiles.find((p) => p.id === form.profile_id);
  const set = (k: keyof WatchlistCreate, v: unknown) => setForm((f) => ({ ...f, [k]: v }));
  const valid = form.title.trim() && form.search_query.trim();

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/50 p-4">
      <div className="w-full max-w-md rounded-xl bg-white dark:bg-gray-900 border border-gray-200 dark:border-gray-700 p-6 shadow-xl">
        <h2 className="mb-4 text-lg font-semibold text-gray-900 dark:text-gray-100">
          {initial ? "Edit watchlist item" : "Add to watchlist"}
        </h2>
        <div className="space-y-3">
          <div>
            <label className="mb-1 block text-sm font-medium text-gray-700 dark:text-gray-300">Show title</label>
            <input value={form.title} onChange={(e) => set("title", e.target.value)} placeholder="e.g. Breaking Bad" className={FIELD} />
          </div>
          <div>
            <label className="mb-1 block text-sm font-medium text-gray-700 dark:text-gray-300">
              Search query <span className="text-xs font-normal text-gray-400 dark:text-gray-500">(e.g. "Breaking Bad")</span>
            </label>
            <input value={form.search_query} onChange={(e) => set("search_query", e.target.value)} placeholder="e.g. Breaking Bad" className={FIELD} />
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
          {profiles.length > 0 && (
            <div>
              <label className="mb-1 block text-sm font-medium text-gray-700 dark:text-gray-300">Quality profile</label>
              <select
                value={form.profile_id ?? ""}
                onChange={(e) => set("profile_id", e.target.value ? Number(e.target.value) : null)}
                className={FIELD}
              >
                <option value="">Custom (set below)</option>
                {profiles.map((p) => <option key={p.id} value={p.id}>{p.name}</option>)}
              </select>
              {profile && (
                <p className="mt-1.5 rounded-lg bg-blue-50 dark:bg-blue-950/40 px-3 py-2 text-xs text-blue-700 dark:text-blue-300">
                  {profileSummary(profile)}
                </p>
              )}
            </div>
          )}
          {!profile && (
          <div className="flex gap-3">
            <div className="flex-1">
              <label className="mb-1 block text-sm font-medium text-gray-700 dark:text-gray-300">Quality</label>
              <select value={form.quality} onChange={(e) => set("quality", e.target.value)} className={FIELD}>
                {QUALITIES.map((q) => <option key={q}>{q}</option>)}
              </select>
            </div>
            <div className="flex-1">
              <label className="mb-1 block text-sm font-medium text-gray-700 dark:text-gray-300">Codec</label>
              <select value={form.codec} onChange={(e) => set("codec", e.target.value)} className={FIELD}>
                {CODECS.map((c) => <option key={c}>{c}</option>)}
              </select>
            </div>
          </div>
          )}
          {paths.length > 0 && (
            <div>
              <label className="mb-1 block text-sm font-medium text-gray-700 dark:text-gray-300">Save to</label>
              <select value={form.download_path_id ?? ""} onChange={(e) => set("download_path_id", e.target.value ? Number(e.target.value) : undefined)} className={FIELD}>
                <option value="">Use default</option>
                {paths.map((p) => {
                  const free = fmtFree(p.free_bytes);
                  return <option key={p.id} value={p.id}>{p.name}{free ? ` (${free})` : ""}</option>;
                })}
              </select>
            </div>
          )}

          {/* Advanced options (a profile supplies these instead) */}
          {!profile && (
          <details className="group">
            <summary className="cursor-pointer select-none text-xs font-medium text-gray-500 dark:text-gray-400 hover:text-gray-700 dark:hover:text-gray-200 list-none flex items-center gap-1">
              <span className="group-open:rotate-90 transition-transform inline-block">›</span>
              Advanced options
            </summary>
            <div className="mt-3 space-y-3 pl-3 border-l-2 border-gray-100 dark:border-gray-800">
              <div>
                <label className="mb-1 block text-sm font-medium text-gray-700 dark:text-gray-300">
                  Max size (MB) <span className="text-xs font-normal text-gray-400">optional</span>
                </label>
                <input
                  type="number"
                  min={1}
                  value={form.max_size_mb ?? ""}
                  onChange={(e) => set("max_size_mb", e.target.value ? Number(e.target.value) : null)}
                  placeholder="e.g. 2000"
                  className={FIELD}
                />
              </div>
              <div>
                <label className="mb-1 block text-sm font-medium text-gray-700 dark:text-gray-300">
                  Preferred release groups <span className="text-xs font-normal text-gray-400">comma-separated, in priority order</span>
                </label>
                <input
                  type="text"
                  value={form.preferred_groups ?? ""}
                  onChange={(e) => set("preferred_groups", e.target.value || null)}
                  placeholder="e.g. YIFY, NTb, SPARKS"
                  className={FIELD}
                />
              </div>
              <label className="flex cursor-pointer items-center gap-2.5">
                <input
                  type="checkbox"
                  checked={form.prefer_season_packs ?? false}
                  onChange={(e) => set("prefer_season_packs", e.target.checked)}
                  className="h-4 w-4 rounded border-gray-300 text-blue-600 focus:ring-blue-500"
                />
                <div>
                  <span className="text-sm font-medium text-gray-700 dark:text-gray-300">Prefer season packs</span>
                  <p className="text-xs text-gray-400 dark:text-gray-500">Download a full season pack when available instead of individual episodes</p>
                </div>
              </label>
            </div>
          </details>
          )}
        </div>
        <div className="mt-5 flex gap-3">
          <button onClick={onClose} className="flex-1 rounded-lg border border-gray-300 dark:border-gray-600 px-4 py-2 text-sm font-medium text-gray-700 dark:text-gray-300 hover:bg-gray-50 dark:hover:bg-gray-800">
            Cancel
          </button>
          <button onClick={() => valid && onSave(form)} disabled={!valid} className="flex-1 rounded-lg bg-blue-600 px-4 py-2 text-sm font-medium text-white hover:bg-blue-700 disabled:opacity-50">
            {initial ? "Save changes" : "Add"}
          </button>
        </div>
      </div>
    </div>
  );
}

// ── Watchlist card with episode tracking ──────────────────────────────────────

function WatchlistCard({ item, paths, profiles, scanning, selected, onToggleSelect, onToggle, onScan, onEdit, onDelete, onRefresh }: {
  item: WatchlistItem;
  paths: DownloadPath[];
  profiles: QualityProfile[];
  scanning: number | "all" | null;
  selected: boolean;
  onToggleSelect: () => void;
  onToggle: () => void;
  onScan: () => void;
  onEdit: () => void;
  onDelete: () => void;
  onRefresh: () => void;
}) {
  const [expanded,       setExpanded]       = useState(false);
  const [episodes,       setEpisodes]       = useState<WatchlistEpisode[] | null>(null);
  const [loadingEps,     setLoadingEps]     = useState(false);
  const [markOpen,       setMarkOpen]       = useState(false);
  const [markSeason,     setMarkSeason]     = useState(item.season);
  const [markEpisode,    setMarkEpisode]    = useState(item.episode);
  const [markMsg,        setMarkMsg]        = useState<{ ok: boolean; text: string } | null>(null);
  const [resetConfirm,   setResetConfirm]   = useState(false);
  const [checkingStatus, setCheckingStatus] = useState(false);

  const pathName = (id?: number) => paths.find((p) => p.id === id)?.name ?? "Default";
  // When a profile is assigned it overrides the item's own preference fields.
  const profile = profiles.find((p) => p.id === item.profile_id);
  const shown = profile ?? item;

  const fetchEpisodes = async () => {
    setLoadingEps(true);
    try {
      setEpisodes(await api.watchlist.episodes.list(item.id));
    } catch {
      setEpisodes([]);
    } finally {
      setLoadingEps(false);
    }
  };

  const handleExpand = () => {
    if (!expanded && episodes === null) fetchEpisodes();
    setExpanded((v) => !v);
  };

  const handleMark = async () => {
    const req: MarkEpisodeRequest = { season: markSeason, episode: markEpisode };
    try {
      await api.watchlist.episodes.mark(item.id, req);
      setMarkMsg({ ok: true, text: `Marked ${epLabel(markSeason, markEpisode)} as downloaded.` });
      setMarkOpen(false);
      fetchEpisodes();
    } catch (err) {
      setMarkMsg({ ok: false, text: err instanceof Error ? err.message : "Failed to mark episode." });
    }
  };

  const handleReset = async () => {
    try {
      const result = await api.watchlist.episodes.reset(item.id);
      setEpisodes([]);
      setResetConfirm(false);
      setMarkMsg({ ok: true, text: `Cleared ${result.deleted} episode record(s).` });
    } catch {
      setMarkMsg({ ok: false, text: "Failed to reset episode tracking." });
    }
  };

  const handleDeleteEp = async (episodeId: number) => {
    try {
      await api.watchlist.episodes.deleteOne(item.id, episodeId);
      setEpisodes((prev) => prev?.filter((e) => e.id !== episodeId) ?? null);
    } catch {
      setMarkMsg({ ok: false, text: "Failed to delete episode record." });
    }
  };

  const handleCheckStatus = async () => {
    setCheckingStatus(true);
    try {
      await api.watchlist.checkShowStatuses();
      onRefresh();
    } finally {
      setCheckingStatus(false);
    }
  };

  const handleResumeAnyway = async () => {
    try {
      await api.watchlist.setOverride(item.id);
      onRefresh();
    } catch { /* ignore */ }
  };

  const handleKeepPaused = async () => {
    try {
      await api.watchlist.removeOverride(item.id);
      onRefresh();
    } catch { /* ignore */ }
  };

  // Group downloaded episodes by season for display
  const bySeason = (episodes ?? []).reduce<Record<number, WatchlistEpisode[]>>((acc, ep) => {
    (acc[ep.season] ??= []).push(ep);
    return acc;
  }, {});

  const epCount = episodes?.length ?? 0;

  return (
    <div
      className={`min-w-0 rounded-xl border border-gray-200 dark:border-gray-700 bg-white dark:bg-gray-900 p-4 shadow-sm transition-opacity ${item.enabled ? "" : "opacity-60"}`}
    >
      {/* Ended alert banner */}
      {item.show_status === "Ended" && !item.enabled && (
        <div className="mb-3 rounded-lg border border-amber-200 dark:border-amber-800 bg-amber-50 dark:bg-amber-950/40 px-3 py-2.5 text-xs">
          <div className="mb-1.5 flex items-center gap-1.5 font-medium text-amber-700 dark:text-amber-300">
            <AlertTriangle className="h-3.5 w-3.5 shrink-0" />
            Show ended — auto-paused
          </div>
          <div className="flex gap-2">
            <button
              onClick={handleKeepPaused}
              className="rounded bg-amber-200 dark:bg-amber-800 px-2 py-0.5 font-medium text-amber-800 dark:text-amber-200 hover:bg-amber-300 dark:hover:bg-amber-700"
            >
              Keep paused
            </button>
            <button
              onClick={handleResumeAnyway}
              className="rounded px-2 py-0.5 text-amber-600 dark:text-amber-400 hover:underline"
            >
              Resume anyway
            </button>
          </div>
        </div>
      )}

      {/* Header */}
      <div className="mb-3 flex items-start justify-between gap-2">
        <div className="flex items-start gap-2.5 min-w-0">
          <input
            type="checkbox"
            checked={selected}
            onChange={onToggleSelect}
            onClick={(e) => e.stopPropagation()}
            className="mt-1 h-4 w-4 shrink-0 rounded border-gray-300 text-blue-600 focus:ring-blue-500"
          />
          <Poster url={item.poster_url} title={item.title} className="h-16 w-11" />
          <div className="min-w-0">
            <div className="flex flex-wrap items-center gap-2">
              <h3 className="font-semibold text-gray-900 dark:text-gray-100 leading-tight [overflow-wrap:anywhere]">{item.title}</h3>
              <ShowStatusBadge status={item.show_status} />
            </div>
            <p className="text-xs text-gray-500 dark:text-gray-400 mt-0.5">
              Looking for: {epLabel(item.season, item.episode)}
              {item.network ? ` · ${item.network}` : ""}
            </p>
            {item.summary && (
              <p className="mt-1 line-clamp-2 text-xs text-gray-400 dark:text-gray-500 [overflow-wrap:anywhere]">{item.summary}</p>
            )}
          </div>
        </div>
        <button
          onClick={onToggle}
          title={item.enabled ? "Disable" : "Enable"}
          className={`flex min-h-[44px] min-w-[44px] shrink-0 items-center justify-center rounded-full transition-colors ${item.enabled ? "text-green-500 hover:text-green-700" : "text-gray-300 dark:text-gray-600 hover:text-gray-500"}`}
        >
          {item.enabled
            ? <CheckCircle2 className="h-5 w-5" />
            : <XCircle className="h-5 w-5" />}
        </button>
      </div>

      {/* Details */}
      <div className="mb-3 space-y-1 text-xs text-gray-500 dark:text-gray-400">
        <div className="flex flex-wrap gap-3">
          {profile && <span>Profile: <strong className="text-gray-700 dark:text-gray-300">{profile.name}</strong></span>}
          <span>Quality: <strong className="text-gray-700 dark:text-gray-300">{shown.quality}</strong></span>
          <span>Codec: <strong className="text-gray-700 dark:text-gray-300">{shown.codec ?? "x265"}</strong></span>
          <span>Save to: <strong className="text-gray-700 dark:text-gray-300">{pathName(item.download_path_id)}</strong></span>
          {shown.max_size_mb && <span>Max: <strong className="text-gray-700 dark:text-gray-300">{shown.max_size_mb >= 1024 ? `${(shown.max_size_mb / 1024).toFixed(1)} GB` : `${shown.max_size_mb} MB`}</strong></span>}
          {shown.prefer_season_packs && <span className="text-blue-600 dark:text-blue-400 font-medium">Season packs</span>}
        </div>
        {shown.preferred_groups && (
          <div>Groups: <strong className="text-gray-700 dark:text-gray-300">{shown.preferred_groups}</strong></div>
        )}
        {profile?.fallback_quality && (
          <div>Falls back to <strong className="text-gray-700 dark:text-gray-300">{profile.fallback_quality}</strong> after {profile.fallback_after_hours}h</div>
        )}
        <div className="flex items-center gap-1">
          <Clock className="h-3 w-3" />
          Checked: {fmtDate(item.last_checked ?? undefined)}
        </div>
        {item.last_found && (
          <div className="flex items-center gap-1 text-green-600 dark:text-green-400">
            <CheckCircle2 className="h-3 w-3" />
            Last found: {fmtDate(item.last_found)}
          </div>
        )}
        {item.show_status_checked_at && (
          <div className="flex items-center gap-1">
            <Tv className="h-3 w-3" />
            Status checked: {fmtAgo(item.show_status_checked_at)}
          </div>
        )}
      </div>

      {/* Actions */}
      <div className="flex gap-2">
        <button
          onClick={onScan}
          disabled={scanning !== null}
          className="flex flex-1 min-h-[44px] items-center justify-center gap-1.5 rounded-lg border border-gray-300 dark:border-gray-600 py-1.5 text-xs font-medium text-gray-600 dark:text-gray-400 hover:bg-gray-100 dark:hover:bg-gray-800 disabled:opacity-50"
        >
          {scanning === item.id ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <Play className="h-3.5 w-3.5" />}
          Scan now
        </button>
        <button
          onClick={handleCheckStatus}
          disabled={checkingStatus}
          title="Check show status on TVMaze"
          className="flex min-h-[44px] min-w-[44px] items-center justify-center rounded-lg border border-gray-300 dark:border-gray-600 text-xs text-gray-600 dark:text-gray-400 hover:bg-gray-100 dark:hover:bg-gray-800 disabled:opacity-50"
        >
          {checkingStatus ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <Search className="h-3.5 w-3.5" />}
        </button>
        <button onClick={onEdit}
          className="flex min-h-[44px] min-w-[44px] items-center justify-center rounded-lg border border-gray-300 dark:border-gray-600 text-xs text-gray-600 dark:text-gray-400 hover:bg-gray-100 dark:hover:bg-gray-800"
          title="Edit">
          <Edit2 className="h-3.5 w-3.5" />
        </button>
        <button onClick={onDelete}
          className="flex min-h-[44px] min-w-[44px] items-center justify-center rounded-lg border border-red-200 dark:border-red-900 text-xs text-red-500 hover:bg-red-50 dark:hover:bg-red-950"
          title="Delete">
          <Trash2 className="h-3.5 w-3.5" />
        </button>
      </div>

      {/* ── Episode tracking section ─────────────────────────────────────── */}
      <div className="mt-3 border-t border-gray-100 dark:border-gray-800 pt-3">
        <button
          onClick={handleExpand}
          className="flex items-center gap-1 text-xs font-medium text-gray-500 dark:text-gray-400 hover:text-gray-700 dark:hover:text-gray-200 transition-colors"
        >
          <ChevronDown className={`h-3.5 w-3.5 transition-transform duration-150 ${expanded ? "rotate-180" : ""}`} />
          Episodes{episodes !== null ? ` (${epCount})` : ""}
        </button>

        {expanded && (
          <div className="mt-2 space-y-3">
            <EpisodeGuide watchlistId={item.id} onChanged={() => { fetchEpisodes(); onRefresh(); }} />
            {episodes !== null && episodes.length > 0 && (
              <div className="border-t border-gray-100 dark:border-gray-800 pt-2 text-xs font-medium text-gray-400 dark:text-gray-500">
                Downloaded by AutoRrent — click to remove a record
              </div>
            )}
            {/* Loading */}
            {loadingEps && (
              <div className="flex items-center gap-1.5 py-1 text-xs text-gray-400">
                <Loader2 className="h-3.5 w-3.5 animate-spin" />
                Loading…
              </div>
            )}

            {/* Episode grid */}
            {!loadingEps && episodes !== null && episodes.length === 0 && (
              <p className="py-1 text-xs text-gray-400 dark:text-gray-600">No episodes tracked yet.</p>
            )}

            {!loadingEps && Object.keys(bySeason).length > 0 && (
              <div className="space-y-2">
                {Object.entries(bySeason)
                  .sort(([a], [b]) => Number(a) - Number(b))
                  .map(([season, eps]) => (
                    <div key={season}>
                      <div className="mb-1 text-xs font-medium text-gray-400 dark:text-gray-500">
                        Season {season}
                      </div>
                      <div className="flex flex-wrap gap-1">
                        {eps
                          .sort((a, b) => a.episode - b.episode)
                          .map((ep) => (
                            <button
                              key={ep.id}
                              title={[
                                ep.torrent_name ?? epLabel(ep.season, ep.episode),
                                `Downloaded: ${new Date(ep.downloaded_at).toLocaleString()}`,
                                "Click to remove",
                              ].join("\n")}
                              onClick={() => handleDeleteEp(ep.id)}
                              className="rounded px-1.5 py-0.5 text-xs font-medium bg-green-100 dark:bg-green-900/60 text-green-700 dark:text-green-300 border border-green-200 dark:border-green-700/60 hover:bg-red-100 dark:hover:bg-red-900/50 hover:border-red-300 dark:hover:border-red-700 hover:text-red-600 dark:hover:text-red-400 transition-colors"
                            >
                              E{String(ep.episode).padStart(2, "0")}
                            </button>
                          ))}
                      </div>
                    </div>
                  ))}
              </div>
            )}

            {/* Feedback message */}
            {markMsg && (
              <p className={`text-xs ${markMsg.ok ? "text-green-600 dark:text-green-400" : "text-red-500 dark:text-red-400"}`}>
                {markMsg.text}
              </p>
            )}

            {/* Mark episode form */}
            {markOpen && (
              <div className="flex flex-wrap items-end gap-2 rounded-lg border border-gray-200 dark:border-gray-700 bg-gray-50 dark:bg-gray-800/50 p-2">
                <div>
                  <label className="mb-0.5 block text-xs text-gray-500 dark:text-gray-400">Season</label>
                  <input
                    type="number"
                    min={1}
                    value={markSeason}
                    onChange={(e) => setMarkSeason(Number(e.target.value))}
                    className="w-16 rounded border border-gray-300 dark:border-gray-600 px-2 py-1 text-xs dark:bg-gray-800 dark:text-gray-100 focus:outline-none focus:ring-1 focus:ring-blue-500"
                  />
                </div>
                <div>
                  <label className="mb-0.5 block text-xs text-gray-500 dark:text-gray-400">Episode</label>
                  <input
                    type="number"
                    min={1}
                    value={markEpisode}
                    onChange={(e) => setMarkEpisode(Number(e.target.value))}
                    className="w-16 rounded border border-gray-300 dark:border-gray-600 px-2 py-1 text-xs dark:bg-gray-800 dark:text-gray-100 focus:outline-none focus:ring-1 focus:ring-blue-500"
                  />
                </div>
                <button
                  onClick={handleMark}
                  className="rounded bg-blue-600 px-2.5 py-1 text-xs font-medium text-white hover:bg-blue-700"
                >
                  Mark
                </button>
                <button
                  onClick={() => { setMarkOpen(false); setMarkMsg(null); }}
                  className="text-xs text-gray-400 hover:text-gray-600 dark:hover:text-gray-200"
                >
                  Cancel
                </button>
              </div>
            )}

            {/* Reset confirmation */}
            {resetConfirm && (
              <div className="rounded-lg border border-red-200 dark:border-red-900 bg-red-50 dark:bg-red-950/50 p-2 text-xs">
                <p className="mb-2 text-red-700 dark:text-red-300">
                  This will allow all episodes to be re-downloaded. Are you sure?
                </p>
                <div className="flex gap-2">
                  <button
                    onClick={handleReset}
                    className="rounded bg-red-600 px-2.5 py-1 font-medium text-white hover:bg-red-700"
                  >
                    Reset
                  </button>
                  <button
                    onClick={() => setResetConfirm(false)}
                    className="text-gray-500 hover:text-gray-700 dark:hover:text-gray-300"
                  >
                    Cancel
                  </button>
                </div>
              </div>
            )}

            {/* Action links */}
            {!markOpen && !resetConfirm && (
              <div className="flex gap-3">
                <button
                  onClick={() => { setMarkOpen(true); setMarkMsg(null); setMarkSeason(item.season); setMarkEpisode(item.episode); }}
                  className="text-xs text-blue-600 dark:text-blue-400 hover:underline"
                >
                  + Mark episode
                </button>
                <button
                  onClick={() => { setResetConfirm(true); setMarkMsg(null); }}
                  className="text-xs text-red-500 dark:text-red-400 hover:underline"
                >
                  Reset tracking
                </button>
              </div>
            )}
          </div>
        )}
      </div>
    </div>
  );
}

// ── Main page ─────────────────────────────────────────────────────────────────

export default function Watchlist() {
  const [items,      setItems]     = useState<WatchlistItem[]>([]);
  const [paths,      setPaths]     = useState<DownloadPath[]>([]);
  const [loading,    setLoading]   = useState(true);
  const [scanning,   setScanning]  = useState<number | "all" | null>(null);
  const [modal,      setModal]     = useState<WatchlistItem | true | null>(null);
  const [toast,      setToast]     = useState<string | null>(null);
  const [selected,   setSelected]  = useState<Set<number>>(new Set());
  const [bulkWorking, setBulkWorking] = useState(false);
  const [search,     setSearch]    = useState("");
  const [profiles,   setProfiles]  = useState<QualityProfile[]>([]);
  const [defaultProfileId, setDefaultProfileId] = useState<number | null>(null);

  const showToast = (msg: string) => { setToast(msg); setTimeout(() => setToast(null), 3000); };

  const load = async () => {
    setLoading(true);
    try {
      const [w, p, pr, st] = await Promise.all([
        api.watchlist.list(), api.paths.list(), api.profiles.list(), api.settings.get(),
      ]);
      setItems(w); setPaths(p); setProfiles(pr);
      setDefaultProfileId(st.default_profile_id ? Number(st.default_profile_id) : null);
    } finally { setLoading(false); }
  };

  useEffect(() => { load(); }, []);

  // Find posters for shows that have none: in rounds (the server paces itself to
  // stay inside TVMaze's rate limit), stopping when everything has been tried or
  // TVMaze is unavailable. Shows it can't match are retried later, not every load.
  useEffect(() => {
    if (loading || !items.some((i) => !i.poster_url)) return;
    let cancelled = false;
    (async () => {
      for (let round = 0; round < 8 && !cancelled; round++) {
        const r = await api.watchlist.refreshMetadata().catch(() => null);
        if (!r || cancelled) return;
        if (r.matched > 0) {
          const w = await api.watchlist.list().catch(() => null);
          if (w && !cancelled) setItems(w);
        }
        if (r.remaining === 0) return;
        if (r.unavailable) {
          // Rate-limited or briefly down: wait out TVMaze's 10-second window, then carry on.
          await new Promise((resolve) => setTimeout(resolve, 10_000));
        } else if (r.checked === 0) {
          return;
        }
      }
    })();
    return () => { cancelled = true; };
  }, [loading]);

  const handleSave = async (data: WatchlistCreate) => {
    if (modal === true) { await api.watchlist.create(data); showToast("Added!"); }
    else if (modal && typeof modal === "object") { await api.watchlist.update(modal.id, data); showToast("Updated!"); }
    setModal(null); load();
  };

  const handleDelete = async (id: number) => {
    if (!confirm("Remove this show from the watchlist?")) return;
    await api.watchlist.delete(id); load();
  };

  const handleToggle = async (item: WatchlistItem) => {
    await api.watchlist.update(item.id, { enabled: !item.enabled }); load();
  };

  const handleScan = async (id?: number) => {
    setScanning(id ?? "all");
    try {
      id ? await api.watchlist.scan(id) : await api.watchlist.scanAll();
      showToast("Scan triggered — results will appear shortly.");
    } finally { setScanning(null); }
  };

  const q = search.trim().toLowerCase();
  const filtered = q
    ? items.filter(
        (i) =>
          i.title.toLowerCase().includes(q) ||
          i.search_query.toLowerCase().includes(q)
      )
    : items;

  const toggleSelect = (id: number) => {
    setSelected((prev) => {
      const next = new Set(prev);
      next.has(id) ? next.delete(id) : next.add(id);
      return next;
    });
  };

  const toggleSelectAll = () => {
    const visibleIds = filtered.map((i) => i.id);
    setSelected((prev) => {
      const allSelected = visibleIds.every((id) => prev.has(id));
      const next = new Set(prev);
      if (allSelected) {
        visibleIds.forEach((id) => next.delete(id));
      } else {
        visibleIds.forEach((id) => next.add(id));
      }
      return next;
    });
  };

  const handleBulkEnable = async (enabled: boolean) => {
    setBulkWorking(true);
    try {
      await Promise.all([...selected].map((id) => api.watchlist.update(id, { enabled })));
      showToast(`${selected.size} item${selected.size !== 1 ? "s" : ""} ${enabled ? "enabled" : "disabled"}.`);
      setSelected(new Set());
      load();
    } finally { setBulkWorking(false); }
  };

  const handleBulkDelete = async () => {
    if (!confirm(`Delete ${selected.size} selected show${selected.size !== 1 ? "s" : ""}? This cannot be undone.`)) return;
    setBulkWorking(true);
    try {
      await Promise.all([...selected].map((id) => api.watchlist.delete(id)));
      showToast(`Deleted ${selected.size} show${selected.size !== 1 ? "s" : ""}.`);
      setSelected(new Set());
      load();
    } finally { setBulkWorking(false); }
  };

  if (loading) return (
    <div className="flex h-full items-center justify-center">
      <Loader2 className="h-6 w-6 animate-spin text-blue-600" />
    </div>
  );

  const visibleIds = filtered.map((i) => i.id);
  const allVisibleSelected = visibleIds.length > 0 && visibleIds.every((id) => selected.has(id));
  const someVisibleSelected = visibleIds.some((id) => selected.has(id)) && !allVisibleSelected;

  return (
    <div className="px-4 py-6 sm:px-6">
      <div className="mb-4 flex flex-wrap items-start justify-between gap-3">
        <div className="min-w-0 basis-full sm:basis-auto sm:flex-1">
          <h1 className="text-2xl font-bold text-gray-900 dark:text-gray-100">Watchlist</h1>
          <p className="mt-1 text-sm text-gray-500 dark:text-gray-400">
            AutoRrent scans these shows and downloads new episodes automatically.
          </p>
        </div>
        <div className="flex flex-wrap gap-2">
          {items.length > 0 && (
            <label className="flex min-h-[40px] items-center gap-1.5 rounded-lg border border-gray-300 dark:border-gray-600 px-3 text-sm text-gray-600 dark:text-gray-400 cursor-pointer hover:bg-gray-100 dark:hover:bg-gray-800">
              <input
                type="checkbox"
                checked={allVisibleSelected}
                ref={(el) => { if (el) el.indeterminate = someVisibleSelected; }}
                onChange={toggleSelectAll}
                className="h-4 w-4 rounded border-gray-300 text-blue-600"
              />
              <span className="text-xs">All</span>
            </label>
          )}
          <button
            onClick={() => handleScan()}
            disabled={scanning !== null}
            className="flex items-center gap-1.5 whitespace-nowrap rounded-lg border border-gray-300 dark:border-gray-600 px-3 py-2 text-sm font-medium text-gray-700 dark:text-gray-300 hover:bg-gray-100 dark:hover:bg-gray-800 disabled:opacity-50"
          >
            {scanning === "all" ? <Loader2 className="h-4 w-4 animate-spin" /> : <RefreshCw className="h-4 w-4" />}
            Scan all
          </button>
          <Link
            to="/browse"
            className="flex items-center gap-1.5 whitespace-nowrap rounded-lg border border-gray-300 dark:border-gray-600 px-3 py-2 text-sm font-medium text-gray-700 dark:text-gray-300 hover:bg-gray-100 dark:hover:bg-gray-800"
          >
            <Search className="h-4 w-4" />
            Browse
          </Link>
          <button
            onClick={() => setModal(true)}
            className="flex items-center gap-1.5 whitespace-nowrap rounded-lg bg-blue-600 px-4 py-2 text-sm font-medium text-white hover:bg-blue-700"
          >
            <Plus className="h-4 w-4" />
            Add show
          </button>
        </div>
      </div>

      {/* Search bar */}
      {items.length > 0 && (
        <div className="mb-4 relative">
          <Search className="absolute left-3 top-1/2 -translate-y-1/2 h-4 w-4 text-gray-400 pointer-events-none" />
          <input
            type="search"
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            placeholder="Filter shows…"
            className="w-full rounded-lg border border-gray-300 dark:border-gray-600 bg-white dark:bg-gray-900 py-2 pl-9 pr-4 text-sm text-gray-900 dark:text-gray-100 placeholder-gray-400 focus:outline-none focus:ring-2 focus:ring-blue-500"
          />
        </div>
      )}

      {/* Bulk action bar */}
      {selected.size > 0 && (
        <div className="mb-4 flex items-center gap-3 rounded-xl border border-blue-200 dark:border-blue-800 bg-blue-50 dark:bg-blue-950/40 px-4 py-3">
          <span className="text-sm font-medium text-blue-700 dark:text-blue-300">
            {selected.size} selected
          </span>
          <div className="ml-auto flex gap-2">
            <button
              onClick={() => handleBulkEnable(true)}
              disabled={bulkWorking}
              className="rounded-lg border border-green-300 dark:border-green-700 bg-white dark:bg-gray-900 px-3 py-1.5 text-xs font-medium text-green-700 dark:text-green-400 hover:bg-green-50 dark:hover:bg-green-950 disabled:opacity-50"
            >
              Enable
            </button>
            <button
              onClick={() => handleBulkEnable(false)}
              disabled={bulkWorking}
              className="rounded-lg border border-gray-300 dark:border-gray-600 bg-white dark:bg-gray-900 px-3 py-1.5 text-xs font-medium text-gray-600 dark:text-gray-400 hover:bg-gray-50 dark:hover:bg-gray-800 disabled:opacity-50"
            >
              Disable
            </button>
            <button
              onClick={handleBulkDelete}
              disabled={bulkWorking}
              className="rounded-lg border border-red-200 dark:border-red-900 bg-white dark:bg-gray-900 px-3 py-1.5 text-xs font-medium text-red-600 dark:text-red-400 hover:bg-red-50 dark:hover:bg-red-950 disabled:opacity-50"
            >
              Delete
            </button>
            <button
              onClick={() => setSelected(new Set())}
              className="text-xs text-gray-400 hover:text-gray-600 dark:hover:text-gray-200"
            >
              Cancel
            </button>
          </div>
        </div>
      )}

      {items.length === 0 ? (
        <div className="py-20 text-center text-gray-400 dark:text-gray-600">
          <Tv className="mx-auto mb-3 h-12 w-12 opacity-30" />
          <p className="font-medium">No shows on your watchlist yet.</p>
          <p className="mt-1 text-sm">
            Click "Add show", <Link to="/browse" className="text-blue-600 dark:text-blue-400 underline">browse what's airing</Link>, or use the Search page.
          </p>
        </div>
      ) : filtered.length === 0 ? (
        <div className="py-16 text-center text-gray-400 dark:text-gray-600">
          <Search className="mx-auto mb-3 h-10 w-10 opacity-30" />
          <p className="font-medium">No shows match "{search}"</p>
          <button onClick={() => setSearch("")} className="mt-2 text-sm text-blue-500 hover:underline">
            Clear filter
          </button>
        </div>
      ) : (
        <>
          {search && (
            <p className="mb-3 text-xs text-gray-400 dark:text-gray-500">
              {filtered.length} of {items.length} show{items.length !== 1 ? "s" : ""} match
            </p>
          )}
          <div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-3">
            {filtered.map((item) => (
              <WatchlistCard
                key={item.id}
                item={item}
                paths={paths}
                profiles={profiles}
                scanning={scanning}
                selected={selected.has(item.id)}
                onToggleSelect={() => toggleSelect(item.id)}
                onToggle={() => handleToggle(item)}
                onScan={() => handleScan(item.id)}
                onEdit={() => setModal(item)}
                onDelete={() => handleDelete(item.id)}
                onRefresh={load}
              />
            ))}
          </div>
        </>
      )}

      {modal !== null && (
        <FormModal initial={modal === true ? undefined : modal} paths={paths} profiles={profiles} defaultProfileId={defaultProfileId} onClose={() => setModal(null)} onSave={handleSave} />
      )}

      {toast && (
        <div className="fixed bottom-20 right-4 z-50 rounded-lg bg-gray-900 dark:bg-gray-700 px-4 py-3 text-sm text-white shadow-lg md:bottom-6 md:right-6">
          {toast}
        </div>
      )}
    </div>
  );
}
