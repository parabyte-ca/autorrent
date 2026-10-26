import { authStore } from "../auth";

const BASE = "/api";

function _authHeader(): Record<string, string> {
  const token = authStore.getToken();
  return token ? { Authorization: `Bearer ${token}` } : {};
}

async function req<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(`${BASE}${path}`, {
    ...init,
    headers: {
      "Content-Type": "application/json",
      ..._authHeader(),
      ...(init?.headers as Record<string, string> ?? {}),
    },
  });
  if (res.status === 401) {
    authStore.clearToken();
    window.location.href = "/login";
    throw new Error("Session expired. Please log in.");
  }
  if (!res.ok) {
    const err = await res.json().catch(() => ({ detail: "Unknown error" }));
    throw new Error(err.detail ?? "Request failed");
  }
  return res.json() as Promise<T>;
}

// For endpoints that return a raw Response (file downloads, multipart uploads)
function authedFetch(path: string, init?: RequestInit): Promise<Response> {
  return fetch(`${BASE}${path}`, {
    ...init,
    headers: { ..._authHeader(), ...(init?.headers as Record<string, string> ?? {}) },
  });
}

// ── Types ─────────────────────────────────────────────────────────────────────

export interface TorrentResult {
  title: string;
  size: string;
  size_bytes: number;
  seeds: number;
  leeches: number;
  magnet: string;
  info_hash?: string;
  quality?: string;
  source: string;
  url?: string;
}

export interface WatchlistItem {
  id: number;
  title: string;
  search_query: string;
  quality: string;
  codec: string;
  season: number;
  episode: number;
  download_path_id?: number;
  enabled: boolean;
  last_checked?: string;
  last_found?: string;
  created_at: string;
  show_status: "Running" | "Ended" | "To Be Determined" | "In Development" | "Unknown" | null;
  show_status_checked_at: string | null;
  tvmaze_id: number | null;
  show_status_override: boolean;
  max_size_mb?: number | null;
  preferred_groups?: string | null;
  prefer_season_packs?: boolean;
  profile_id?: number | null;
  poster_url?: string | null;
  summary?: string | null;
  network?: string | null;
  premiered?: string | null;
  metadata_checked_at?: string | null;
}

export interface QualityProfile {
  id: number;
  name: string;
  quality: string;
  codec: string;
  max_size_mb: number | null;
  preferred_groups: string | null;
  prefer_season_packs: boolean;
  fallback_quality: string | null;
  fallback_after_hours: number;
  watchlist_count: number;
}

export type ProfileInput = Omit<QualityProfile, "id" | "watchlist_count">;

export interface JackettTestResult {
  ok: boolean;
  kind?: string;
  version?: string | null;
  indexers?: number;
  error?: string;
}

export interface WatchlistCreate {
  title: string;
  search_query: string;
  quality: string;
  codec: string;
  season: number;
  episode: number;
  download_path_id?: number;
  enabled: boolean;
  max_size_mb?: number | null;
  preferred_groups?: string | null;
  prefer_season_packs?: boolean;
  profile_id?: number | null;
}

export interface Download {
  id: number;
  title: string;
  torrent_hash?: string;
  size_bytes?: number;
  status: string;
  download_path?: string;
  watchlist_id?: number;
  created_at: string;
  progress?: number;
  eta?: number;
  dlspeed?: number;
  is_stale?: boolean;
}

export interface DownloadPath {
  id: number;
  name: string;
  path: string;
  is_default: boolean;
  free_bytes?: number | null;
  total_bytes?: number | null;
  low_space?: boolean;
}

export type Settings = Record<string, string>;

export interface PlexLibrary {
  key: string;
  title: string;
  type: string;
}

export interface PlexTestResult {
  ok: boolean;
  error?: string;
  libraries?: PlexLibrary[];
}

export interface JellyfinTestResult {
  ok: boolean;
  error?: string;
  server_name?: string;
  version?: string;
}

export interface BackupMeta {
  created_at: string;
  app_version: string;
  db_path: string;
}

export interface RestoreResponse {
  ok: boolean;
  restored_from?: string;
  message?: string;
  error?: string;
}

export interface DownloadHistoryItem {
  id: number;
  name: string;
  source: "manual" | "watchlist";
  indexer: string | null;
  folder: string | null;
  torrent_hash: string | null;
  size_bytes: number | null;
  size_human: string;
  added_at: string;        // ISO 8601 with Z suffix
  completed_at: string | null;
  status: "downloading" | "completed" | "failed";
  watchlist_id: number | null;
  error_msg: string | null;
}

export interface HistoryResponse {
  items: DownloadHistoryItem[];
  total: number;
}

export interface WatchlistEpisode {
  id: number;
  watchlist_id: number;
  season: number;
  episode: number;
  downloaded_at: string;       // ISO 8601 with Z suffix
  torrent_hash: string | null;
  torrent_name: string | null;
}

export type EpisodeStatus = "downloaded" | "missing" | "upcoming";

export interface GuideEpisode {
  number: number;
  name: string;
  airs_at: string | null;
  status: EpisodeStatus;
  torrent_name: string | null;
}

export interface GuideSeason {
  season: number;
  episodes: GuideEpisode[];
  total: number;
  aired: number;
  downloaded: number;
}

export interface EpisodeGuide {
  available: boolean;
  pointer: { season: number; episode: number };
  seasons: GuideSeason[];
}

export interface EpisodeSearchResult {
  status: "started" | "not_found" | "already_downloaded" | "duplicate";
  message: string;
  title?: string;
}

export interface CalendarEntry {
  watchlist_id: number;
  title: string;
  poster_url: string | null;
  enabled: boolean;
  season: number;
  episode: number;
  name: string;
  airs_at: string;
  status: EpisodeStatus;
}

export interface CalendarResponse {
  entries: CalendarEntry[];
  unmatched: number;
}

export interface BrowseShow {
  tvmaze_id: number;
  name: string;
  poster_url: string | null;
  network: string | null;
  premiered: string | null;
  status: string | null;
  genres: string[];
  rating: number | null;
  summary: string | null;
  next_airs: string | null;
  next_episode: string | null;
  season_premiere: boolean;
  on_watchlist: boolean;
  watchlist_id: number | null;
}

export interface BrowseResponse {
  mode: "airing" | "search";
  country: string;
  shows: BrowseShow[];
}

export interface TraktResult {
  ok: boolean;
  error: string | null;
  at: string;
  checked: number;
  added: string[];
  existing: number;
  unmatched: string[];
  deferred: number;
}

export interface TraktStatus {
  configured: boolean;
  enabled: boolean;
  last_result: TraktResult | null;
  seen_count: number;
  max_new_per_run: number;
}

export interface MarkEpisodeRequest {
  season: number;
  episode: number;
  torrent_name?: string;
}

export interface DuplicateCheckResult {
  is_duplicate: boolean;
  match_type: "hash" | "name" | "active" | null;
  matched_name: string | null;
  matched_at: string | null;
}

export interface LatestRelease {
  version: string;
  name: string;
  notes: string;
  url: string;
  published_at: string;
}

export interface HelperInfo {
  configured: boolean;
  connected: boolean;
  last_seen: string | null;
  every_minutes: number;
}

export interface UpdateStatus {
  current_version: string;
  latest_release: LatestRelease | null;
  update_available: boolean;
  checked_at: string | null;
  check_error: string | null;
  helper: HelperInfo;
  upgrade_state: "requested" | "running" | "succeeded" | "failed" | null;
  upgrade_message: string | null;
  can_upgrade: boolean;
  previous_version: string | null;
  can_rollback: boolean;
  helper_install_cmd: string;
  upgrade_log?: string;
}

// ── API client ────────────────────────────────────────────────────────────────

export const api = {
  search(q: string, indexer = "all", quality?: string, codec = "x265", filterAdult = true) {
    const p = new URLSearchParams({ q, indexer, codec, filter_adult: String(filterAdult) });
    if (quality && quality !== "Any") p.set("quality", quality);
    return req<TorrentResult[]>(`/search?${p}`);
  },

  watchlist: {
    list: () => req<WatchlistItem[]>("/watchlist"),
    create: (data: WatchlistCreate) =>
      req<WatchlistItem>("/watchlist", { method: "POST", body: JSON.stringify(data) }),
    update: (id: number, data: Partial<WatchlistCreate>) =>
      req<WatchlistItem>(`/watchlist/${id}`, { method: "PUT", body: JSON.stringify(data) }),
    delete: (id: number) => req(`/watchlist/${id}`, { method: "DELETE" }),
    scan: (id: number) => req(`/watchlist/${id}/scan`, { method: "POST" }),
    scanAll: () => req("/scan", { method: "POST" }),
    episodes: {
      list: (id: number) =>
        req<WatchlistEpisode[]>(`/watchlist/${id}/episodes`),
      mark: (id: number, data: MarkEpisodeRequest) =>
        req<WatchlistEpisode>(`/watchlist/${id}/episodes`, {
          method: "POST",
          body: JSON.stringify(data),
        }),
      deleteOne: (watchlistId: number, episodeId: number) =>
        req(`/watchlist/${watchlistId}/episodes/${episodeId}`, { method: "DELETE" }),
      reset: (id: number) =>
        req<{ deleted: number }>(`/watchlist/${id}/episodes?confirm=true`, { method: "DELETE" }),
    },
    guide: (id: number) => req<EpisodeGuide>(`/watchlist/${id}/guide`),
    searchEpisode: (id: number, season: number, episode: number) =>
      req<EpisodeSearchResult>(`/watchlist/${id}/episodes/${season}/${episode}/search`, { method: "POST" }),
    refreshMetadata: () =>
      req<{ checked: number; matched: number; remaining: number; unavailable: boolean }>("/watchlist/refresh-metadata", { method: "POST" }),
    addFromTvmaze: (data: { tvmaze_id: number; start?: "next" | "beginning" }) =>
      req<WatchlistItem>("/watchlist/from-tvmaze", { method: "POST", body: JSON.stringify(data) }),
    checkShowStatuses: () =>
      req<{ ok: boolean; message: string }>("/watchlist/check-show-statuses", { method: "POST" }),
    setOverride: (id: number) =>
      req<WatchlistItem>(`/watchlist/${id}/override-status`, { method: "POST" }),
    removeOverride: (id: number) =>
      req<WatchlistItem>(`/watchlist/${id}/override-status`, { method: "DELETE" }),
  },

  downloads: {
    list: () => req<Download[]>("/downloads"),
    add: (data: { magnet: string; title: string; download_path_id?: number; indexer?: string; force?: boolean }) =>
      req("/downloads", { method: "POST", body: JSON.stringify(data) }),
    delete: (id: number) => req(`/downloads/${id}`, { method: "DELETE" }),
    clearFinished: () => req<{ ok: boolean; deleted: number }>("/downloads", { method: "DELETE" }),
    checkDuplicate: (data: { torrent_hash: string | null; torrent_name: string }) =>
      req<DuplicateCheckResult>("/downloads/check-duplicate", { method: "POST", body: JSON.stringify(data) }),
  },

  history: {
    list: (params: string) => req<HistoryResponse>(`/history?${params}`),
    /** Fetch the full history as a CSV file; returns a raw Response. */
    export: (): Promise<Response> => authedFetch(`/history/export`),
    deleteOne: (id: number) => req(`/history/${id}`, { method: "DELETE" }),
    clearAll: () => req<{ deleted: number }>("/history?confirm=true", { method: "DELETE" }),
  },

  paths: {
    list: () => req<DownloadPath[]>("/paths"),
    create: (data: { name: string; path: string; is_default: boolean }) =>
      req<DownloadPath>("/paths", { method: "POST", body: JSON.stringify(data) }),
    update: (id: number, data: Partial<{ name: string; path: string; is_default: boolean }>) =>
      req<DownloadPath>(`/paths/${id}`, { method: "PUT", body: JSON.stringify(data) }),
    delete: (id: number) => req(`/paths/${id}`, { method: "DELETE" }),
  },

  trakt: {
    status: () => req<TraktStatus>("/trakt/status"),
    test: (data: { client_id: string; username: string }) =>
      req<{ ok: boolean; shows?: number; error?: string }>("/trakt/test", { method: "POST", body: JSON.stringify(data) }),
    sync: () => req<TraktResult>("/trakt/sync", { method: "POST" }),
    forgetHistory: () => req<{ ok: boolean; cleared: number }>("/trakt/seen", { method: "DELETE" }),
  },

  onboarding: {
    status: () => req<{ needed: boolean }>("/onboarding/status"),
    complete: () => req<{ ok: boolean }>("/onboarding/complete", { method: "POST" }),
  },

  calendar: {
    get: (days = 30, back = 7) => req<CalendarResponse>(`/calendar?days=${days}&back=${back}`),
  },

  browse: {
    get: (q = "") => req<BrowseResponse>(`/browse${q ? `?q=${encodeURIComponent(q)}` : ""}`),
  },

  profiles: {
    list: () => req<QualityProfile[]>("/profiles"),
    create: (data: ProfileInput) =>
      req<QualityProfile>("/profiles", { method: "POST", body: JSON.stringify(data) }),
    update: (id: number, data: Partial<ProfileInput>) =>
      req<QualityProfile>(`/profiles/${id}`, { method: "PUT", body: JSON.stringify(data) }),
    delete: (id: number) =>
      req<{ ok: boolean; detached: number }>(`/profiles/${id}`, { method: "DELETE" }),
  },

  backup: {
    /** Fetch the export ZIP; returns the raw Response so the caller can stream
     *  the blob — do not pass through req<T>() which calls .json(). */
    export: (): Promise<Response> => authedFetch(`/backup/export`),
    /** Upload a backup ZIP; returns the raw Response so the caller can inspect
     *  both success JSON and error JSON without an intermediate throw. */
    restore: (file: File): Promise<Response> => {
      const form = new FormData();
      form.append("file", file);
      return authedFetch(`/backup/restore`, { method: "POST", body: form });
    },
  },

  auth: {
    status: () => req<{ auth_required: boolean }>("/auth/status"),
    login: (password: string) =>
      req<{ token: string }>("/auth/login", {
        method: "POST",
        body: JSON.stringify({ password }),
      }),
    revoke: () => req<{ ok: boolean }>("/auth/revoke", { method: "POST" }),
  },

  updates: {
    status: () => req<UpdateStatus>("/admin/updates"),
    check: () => req<UpdateStatus>("/admin/updates/check", { method: "POST" }),
    upgrade: (version: string, rollback = false) =>
      req<UpdateStatus>("/admin/updates/upgrade", {
        method: "POST",
        body: JSON.stringify({ version, rollback }),
      }),
  },

  settings: {
    get: () => req<Settings>("/settings"),
    update: (data: Partial<Settings>) =>
      req<Settings>("/settings", { method: "PUT", body: JSON.stringify(data) }),
    testQbit: () =>
      req<{ success: boolean; message: string }>("/settings/test-qbit", { method: "POST" }),
    testPlex: (data: { url: string; token: string; library_key?: string }) =>
      req<PlexTestResult>("/settings/test-plex", { method: "POST", body: JSON.stringify(data) }),
    testTmdb: (api_key: string) =>
      req<{ ok: boolean; message: string }>("/settings/test-tmdb", { method: "POST", body: JSON.stringify({ api_key }) }),
    testJackett: (data: { url: string; api_key: string }) =>
      req<JackettTestResult>("/settings/test-jackett", { method: "POST", body: JSON.stringify(data) }),
    testJellyfin: (data: { url: string; api_key: string }) =>
      req<JellyfinTestResult>("/settings/test-jellyfin", { method: "POST", body: JSON.stringify(data) }),
    testDigest: (data: {
      digest_smtp_host: string; digest_smtp_port: string; digest_smtp_user: string;
      digest_smtp_password: string; digest_from_email: string; digest_recipients: string;
      plex_url: string; plex_token: string; digest_excluded_libs: string;
    }) =>
      req<{ ok: boolean; message?: string; error?: string }>("/settings/test-digest", { method: "POST", body: JSON.stringify(data) }),
  },
};
