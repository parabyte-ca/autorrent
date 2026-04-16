const BASE = "/api";

async function req<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(`${BASE}${path}`, {
    headers: { "Content-Type": "application/json" },
    ...init,
  });
  if (!res.ok) {
    const err = await res.json().catch(() => ({ detail: "Unknown error" }));
    throw new Error(err.detail ?? "Request failed");
  }
  return res.json() as Promise<T>;
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
}

export interface DownloadPath {
  id: number;
  name: string;
  path: string;
  is_default: boolean;
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

export interface MarkEpisodeRequest {
  season: number;
  episode: number;
  torrent_name?: string;
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
    checkShowStatuses: () =>
      req<{ ok: boolean; message: string }>("/watchlist/check-show-statuses", { method: "POST" }),
    setOverride: (id: number) =>
      req<WatchlistItem>(`/watchlist/${id}/override-status`, { method: "POST" }),
    removeOverride: (id: number) =>
      req<WatchlistItem>(`/watchlist/${id}/override-status`, { method: "DELETE" }),
  },

  downloads: {
    list: () => req<Download[]>("/downloads"),
    add: (data: { magnet: string; title: string; download_path_id?: number; indexer?: string }) =>
      req("/downloads", { method: "POST", body: JSON.stringify(data) }),
    delete: (id: number) => req(`/downloads/${id}`, { method: "DELETE" }),
  },

  history: {
    list: (params: string) => req<HistoryResponse>(`/history?${params}`),
    /** Fetch the full history as a CSV file; returns a raw Response. */
    export: (): Promise<Response> => fetch(`${BASE}/history/export`),
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

  backup: {
    /** Fetch the export ZIP; returns the raw Response so the caller can stream
     *  the blob — do not pass through req<T>() which calls .json(). */
    export: (): Promise<Response> => fetch(`${BASE}/backup/export`),
    /** Upload a backup ZIP; returns the raw Response so the caller can inspect
     *  both success JSON and error JSON without an intermediate throw. */
    restore: (file: File): Promise<Response> => {
      const form = new FormData();
      form.append("file", file);
      return fetch(`${BASE}/backup/restore`, { method: "POST", body: form });
    },
  },

  settings: {
    get: () => req<Settings>("/settings"),
    update: (data: Partial<Settings>) =>
      req<Settings>("/settings", { method: "PUT", body: JSON.stringify(data) }),
    testQbit: () =>
      req<{ success: boolean; message: string }>("/settings/test-qbit", { method: "POST" }),
    testPlex: (data: { url: string; token: string; library_key?: string }) =>
      req<PlexTestResult>("/settings/test-plex", { method: "POST", body: JSON.stringify(data) }),
    testJellyfin: (data: { url: string; api_key: string }) =>
      req<JellyfinTestResult>("/settings/test-jellyfin", { method: "POST", body: JSON.stringify(data) }),
  },
};
