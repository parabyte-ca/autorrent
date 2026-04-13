import { useEffect, useState } from "react";
import { AlertCircle, CheckCircle2, Edit2, FolderOpen, Loader2, Plus, Save, Star, Trash2 } from "lucide-react";
import { api, type DownloadPath, type JellyfinTestResult, type PlexLibrary, type PlexTestResult, type Settings as SettingsType } from "../api/client";

const FIELD = [
  "w-full rounded-lg border border-gray-300 dark:border-gray-600 px-3 py-2 text-sm",
  "focus:outline-none focus:ring-2 focus:ring-blue-500",
  "dark:bg-gray-800 dark:text-gray-100",
].join(" ");

// ── Health types & helpers ────────────────────────────────────────────────────

type HealthStatus = {
  status: "ok" | "degraded";
  db_ok: boolean;
  uptime_seconds: number;
  version: string;
} | null;

function fmtUptime(s: number): string {
  const d = Math.floor(s / 86400);
  const h = Math.floor((s % 86400) / 3600);
  const m = Math.floor((s % 3600) / 60);
  const parts: string[] = [];
  if (d > 0) parts.push(`${d}d`);
  parts.push(`${h}h`);
  parts.push(`${m}m`);
  return parts.join(" ");
}

// ── Reusable field ────────────────────────────────────────────────────────────

function Field({ label, hint, type = "text", value, onChange, placeholder }: {
  label: string; hint?: string; type?: string;
  value: string; onChange: (v: string) => void; placeholder?: string;
}) {
  return (
    <div>
      <label className="mb-1 block text-sm font-medium text-gray-700 dark:text-gray-300">{label}</label>
      {hint && <p className="mb-1 text-xs text-gray-500 dark:text-gray-400">{hint}</p>}
      <input type={type} value={value} onChange={(e) => onChange(e.target.value)} placeholder={placeholder} className={FIELD} />
    </div>
  );
}

// ── Path modal ────────────────────────────────────────────────────────────────

function PathModal({ initial, onClose, onSave }: {
  initial?: DownloadPath;
  onClose: () => void;
  onSave: (d: { name: string; path: string; is_default: boolean }) => void;
}) {
  const [form, setForm] = useState({
    name: initial?.name ?? "",
    path: initial?.path ?? "",
    is_default: initial?.is_default ?? false,
  });
  const valid = form.name.trim() && form.path.trim();

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/50 p-4">
      <div className="w-full max-w-sm rounded-xl bg-white dark:bg-gray-900 border border-gray-200 dark:border-gray-700 p-6 shadow-xl">
        <h2 className="mb-4 text-lg font-semibold text-gray-900 dark:text-gray-100">
          {initial ? "Edit folder" : "Add download folder"}
        </h2>
        <div className="space-y-3">
          <div>
            <label className="mb-1 block text-sm font-medium text-gray-700 dark:text-gray-300">Display name</label>
            <input value={form.name} onChange={(e) => setForm((f) => ({ ...f, name: e.target.value }))} placeholder="e.g. TV Shows" className={FIELD} />
          </div>
          <div>
            <label className="mb-1 block text-sm font-medium text-gray-700 dark:text-gray-300">Linux path</label>
            <input value={form.path} onChange={(e) => setForm((f) => ({ ...f, path: e.target.value }))} placeholder="/mnt/nas/tv" className={`${FIELD} font-mono`} />
          </div>
          <label className="flex cursor-pointer items-center gap-2">
            <input type="checkbox" checked={form.is_default} onChange={(e) => setForm((f) => ({ ...f, is_default: e.target.checked }))} className="h-4 w-4 rounded border-gray-300 text-blue-600" />
            <span className="text-sm text-gray-700 dark:text-gray-300">Set as default folder</span>
          </label>
        </div>
        <div className="mt-5 flex gap-3">
          <button onClick={onClose} className="flex-1 rounded-lg border border-gray-300 dark:border-gray-600 px-4 py-2 text-sm font-medium text-gray-700 dark:text-gray-300 hover:bg-gray-50 dark:hover:bg-gray-800">
            Cancel
          </button>
          <button onClick={() => valid && onSave(form)} disabled={!valid} className="flex-1 rounded-lg bg-blue-600 px-4 py-2 text-sm font-medium text-white hover:bg-blue-700 disabled:opacity-50">
            Save
          </button>
        </div>
      </div>
    </div>
  );
}

// ── Section card ──────────────────────────────────────────────────────────────

function Section({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <section className="rounded-xl border border-gray-200 dark:border-gray-700 bg-white dark:bg-gray-900 p-5 shadow-sm">
      <h2 className="mb-4 text-base font-semibold text-gray-900 dark:text-gray-100">{title}</h2>
      {children}
    </section>
  );
}

// ── Main page ─────────────────────────────────────────────────────────────────

export default function Settings() {
  const [settings,         setSettings]         = useState<SettingsType>({});
  const [paths,            setPaths]            = useState<DownloadPath[]>([]);
  const [loading,          setLoading]          = useState(true);
  const [saving,           setSaving]           = useState(false);
  const [testing,          setTesting]          = useState(false);
  const [testResult,       setTestResult]       = useState<{ success: boolean; message: string } | null>(null);
  const [pathModal,        setPathModal]        = useState<DownloadPath | true | null>(null);
  const [toast,            setToast]            = useState<string | null>(null);
  const [plexLibraries,    setPlexLibraries]    = useState<PlexLibrary[]>([]);
  const [plexTestResult,   setPlexTestResult]   = useState<PlexTestResult | null>(null);
  const [testingPlex,      setTestingPlex]      = useState(false);
  const [jellyfinTestResult, setJellyfinTestResult] = useState<JellyfinTestResult | null>(null);
  const [testingJellyfin,  setTestingJellyfin]  = useState(false);
  const [health,           setHealth]           = useState<HealthStatus>(null);

  const showToast = (msg: string) => { setToast(msg); setTimeout(() => setToast(null), 3000); };

  const load = async () => {
    const [s, p] = await Promise.all([api.settings.get(), api.paths.list()]);
    setSettings(s); setPaths(p); setLoading(false);
  };
  useEffect(() => { load(); }, []);

  const fetchHealth = async () => {
    try {
      const res = await fetch("/health");
      if (res.ok) setHealth(await res.json());
      else setHealth(null);
    } catch {
      setHealth(null);
    }
  };
  useEffect(() => {
    fetchHealth();
    const id = setInterval(fetchHealth, 30_000);
    return () => clearInterval(id);
  }, []);

  const set = (k: string, v: string) => setSettings((s) => ({ ...s, [k]: v }));

  const handleSave = async () => {
    setSaving(true);
    try { await api.settings.update(settings); showToast("Settings saved!"); }
    catch (e: unknown) { showToast(`Error: ${(e as Error).message}`); }
    finally { setSaving(false); }
  };

  const handleTestQbit = async () => {
    setTesting(true); setTestResult(null);
    try { setTestResult(await api.settings.testQbit()); }
    finally { setTesting(false); }
  };

  const handleTestPlex = async () => {
    setTestingPlex(true); setPlexTestResult(null);
    try {
      const result = await api.settings.testPlex({
        url: settings.plex_url ?? "",
        token: settings.plex_token ?? "",
        library_key: settings.plex_library_key || undefined,
      });
      setPlexTestResult(result);
      if (result.ok && result.libraries) setPlexLibraries(result.libraries);
    } finally { setTestingPlex(false); }
  };

  const handleTestJellyfin = async () => {
    setTestingJellyfin(true); setJellyfinTestResult(null);
    try {
      const result = await api.settings.testJellyfin({
        url: settings.jellyfin_url ?? "",
        api_key: settings.jellyfin_api_key ?? "",
      });
      setJellyfinTestResult(result);
    } finally { setTestingJellyfin(false); }
  };

  const handlePathSave = async (data: { name: string; path: string; is_default: boolean }) => {
    if (pathModal === true) await api.paths.create(data);
    else if (pathModal && typeof pathModal === "object") await api.paths.update(pathModal.id, data);
    setPathModal(null); load();
  };

  const handlePathDelete = async (id: number) => {
    if (!confirm("Remove this folder?")) return;
    await api.paths.delete(id); load();
  };

  if (loading) return (
    <div className="flex h-full items-center justify-center">
      <Loader2 className="h-6 w-6 animate-spin text-blue-600" />
    </div>
  );

  return (
    <div className="p-6 max-w-2xl">
      <div className="mb-6">
        <h1 className="text-2xl font-bold text-gray-900 dark:text-gray-100">Settings</h1>
        <p className="mt-1 text-sm text-gray-500 dark:text-gray-400">Configure your qBittorrent connection and preferences.</p>
      </div>

      {/* System status bar */}
      <div className="mb-6 flex items-center gap-2.5 rounded-lg border border-gray-200 dark:border-gray-700 bg-white dark:bg-gray-900 px-4 py-3 text-sm shadow-sm">
        <span className={`h-2 w-2 shrink-0 rounded-full ${
          health === null ? "bg-gray-400 dark:bg-gray-600" :
          health.status === "ok" ? "bg-green-500" : "bg-amber-400"
        }`} />
        <span className="font-medium text-gray-700 dark:text-gray-300">
          {health === null ? "Unreachable" : health.status === "ok" ? "System healthy" : "Degraded"}
        </span>
        {health !== null && (
          <>
            <span className="text-gray-300 dark:text-gray-600">·</span>
            <span className="text-gray-400 dark:text-gray-500">Up {fmtUptime(health.uptime_seconds)}</span>
            <span className="text-gray-300 dark:text-gray-600">·</span>
            <span className="text-gray-400 dark:text-gray-500">v{health.version}</span>
          </>
        )}
      </div>

      <div className="space-y-6">
        {/* qBittorrent */}
        <Section title="qBittorrent Connection">
          <div className="grid gap-3 sm:grid-cols-2">
            <Field label="Host" value={settings.qbit_host ?? ""} onChange={(v) => set("qbit_host", v)} placeholder="192.168.1.100" />
            <Field label="Port" value={settings.qbit_port ?? ""} onChange={(v) => set("qbit_port", v)} placeholder="8080" />
            <Field label="Username" value={settings.qbit_username ?? ""} onChange={(v) => set("qbit_username", v)} placeholder="admin" />
            <Field label="Password" type="password" value={settings.qbit_password ?? ""} onChange={(v) => set("qbit_password", v)} placeholder="••••••••" />
            <div className="sm:col-span-2">
              <Field label="Category" hint="Torrents added by AutoRrent will use this category in qBittorrent." value={settings.qbit_category ?? ""} onChange={(v) => set("qbit_category", v)} placeholder="autorrent" />
            </div>
          </div>
          <div className="mt-4 flex items-center gap-3">
            <button onClick={handleTestQbit} disabled={testing}
              className="flex items-center gap-1.5 rounded-lg border border-gray-300 dark:border-gray-600 px-4 py-2 text-sm font-medium text-gray-700 dark:text-gray-300 hover:bg-gray-100 dark:hover:bg-gray-800 disabled:opacity-50">
              {testing && <Loader2 className="h-4 w-4 animate-spin" />}
              Test connection
            </button>
            {testResult && (
              <span className={`flex items-center gap-1.5 text-sm font-medium ${testResult.success ? "text-green-600 dark:text-green-400" : "text-red-600 dark:text-red-400"}`}>
                {testResult.success ? <CheckCircle2 className="h-4 w-4" /> : <AlertCircle className="h-4 w-4" />}
                {testResult.message}
              </span>
            )}
          </div>
        </Section>

        {/* Download folders */}
        <Section title="Download Folders">
          <p className="mb-4 text-xs text-gray-500 dark:text-gray-400">
            These are the folders users can choose when downloading. Paths must be valid on the machine running qBittorrent.
          </p>
          <div className="mb-4 flex justify-end">
            <button onClick={() => setPathModal(true)}
              className="flex items-center gap-1.5 rounded-lg bg-blue-600 px-3 py-1.5 text-sm font-medium text-white hover:bg-blue-700">
              <Plus className="h-4 w-4" />
              Add folder
            </button>
          </div>
          {paths.length === 0 ? (
            <div className="rounded-lg border-2 border-dashed border-gray-200 dark:border-gray-700 py-8 text-center text-sm text-gray-400 dark:text-gray-600">
              <FolderOpen className="mx-auto mb-2 h-8 w-8 opacity-40" />
              No folders configured yet. Add one above.
            </div>
          ) : (
            <ul className="divide-y divide-gray-100 dark:divide-gray-800">
              {paths.map((p) => (
                <li key={p.id} className="flex items-center gap-3 py-2.5">
                  <div className="flex-1 min-w-0">
                    <div className="flex items-center gap-1.5">
                      <span className="text-sm font-medium text-gray-900 dark:text-gray-100">{p.name}</span>
                      {p.is_default && (
                        <span className="inline-flex items-center gap-0.5 rounded-full bg-yellow-100 dark:bg-yellow-900/40 px-1.5 py-0.5 text-[10px] font-semibold text-yellow-700 dark:text-yellow-400">
                          <Star className="h-2.5 w-2.5" /> Default
                        </span>
                      )}
                    </div>
                    <p className="text-xs text-gray-400 dark:text-gray-500 font-mono truncate">{p.path}</p>
                  </div>
                  <button onClick={() => setPathModal(p)} className="rounded-lg p-1.5 text-gray-400 hover:bg-gray-100 dark:hover:bg-gray-800">
                    <Edit2 className="h-3.5 w-3.5" />
                  </button>
                  <button onClick={() => handlePathDelete(p.id)} className="rounded-lg p-1.5 text-gray-400 hover:bg-red-50 dark:hover:bg-red-950 hover:text-red-500">
                    <Trash2 className="h-3.5 w-3.5" />
                  </button>
                </li>
              ))}
            </ul>
          )}
        </Section>

        {/* Auto-scanner */}
        <Section title="Auto-scanner">
          <div className="grid gap-3 sm:grid-cols-2">
            <div>
              <label className="mb-1 block text-sm font-medium text-gray-700 dark:text-gray-300">Scan interval (minutes)</label>
              <p className="mb-1 text-xs text-gray-500 dark:text-gray-400">How often AutoRrent checks for new episodes.</p>
              <input type="number" min={5} value={settings.scan_interval_minutes ?? "60"} onChange={(e) => set("scan_interval_minutes", e.target.value)} className={FIELD} />
            </div>
            <div>
              <label className="mb-1 block text-sm font-medium text-gray-700 dark:text-gray-300">Minimum seeds</label>
              <p className="mb-1 text-xs text-gray-500 dark:text-gray-400">Only download if a torrent has at least this many seeds.</p>
              <input type="number" min={0} value={settings.min_seeds ?? "3"} onChange={(e) => set("min_seeds", e.target.value)} className={FIELD} />
            </div>
          </div>

          <div className="mt-4 border-t border-gray-100 dark:border-gray-800 pt-4">
            <label className="flex cursor-pointer items-start gap-3">
              <input
                type="checkbox"
                checked={settings.remove_on_complete === "true"}
                onChange={(e) => set("remove_on_complete", e.target.checked ? "true" : "false")}
                className="mt-0.5 h-4 w-4 rounded border-gray-300 text-blue-600"
              />
              <div>
                <span className="text-sm font-medium text-gray-700 dark:text-gray-300">
                  Remove from qBittorrent when download completes
                </span>
                <p className="mt-0.5 text-xs text-gray-500 dark:text-gray-400">
                  Once a torrent finishes downloading and starts seeding, AutoRrent will remove it from
                  qBittorrent automatically. Your downloaded files are kept — only the torrent entry is removed.
                </p>
              </div>
            </label>
          </div>
        </Section>

        {/* Jackett / Prowlarr */}
        <Section title="Jackett / Prowlarr">
          <p className="mb-4 text-xs text-gray-500 dark:text-gray-400">
            Optional. Connect to Jackett or Prowlarr to search hundreds of additional indexers.
            Leave blank to use only the built-in NYAA and TPB sources.
          </p>
          <div className="grid gap-3 sm:grid-cols-2">
            <Field label="URL" value={settings.jackett_url ?? ""} onChange={(v) => set("jackett_url", v)} placeholder="http://localhost:9117" />
            <Field label="API Key" value={settings.jackett_api_key ?? ""} onChange={(v) => set("jackett_api_key", v)} placeholder="your-api-key" />
          </div>
        </Section>

        {/* Plex */}
        <Section title="Plex Media Server">
          <p className="mb-4 text-xs text-gray-500 dark:text-gray-400">
            Optional. AutoRrent will refresh your Plex library whenever a download completes.
          </p>
          <div className="mb-4 border-b border-gray-100 dark:border-gray-800 pb-4">
            <label className="flex cursor-pointer items-start gap-3">
              <input
                type="checkbox"
                checked={settings.plex_enabled === "true"}
                onChange={(e) => set("plex_enabled", e.target.checked ? "true" : "false")}
                className="mt-0.5 h-4 w-4 rounded border-gray-300 text-blue-600"
              />
              <span className="text-sm font-medium text-gray-700 dark:text-gray-300">Enable Plex library refresh</span>
            </label>
          </div>
          <div className={`space-y-3 ${settings.plex_enabled !== "true" ? "opacity-50 pointer-events-none" : ""}`}>
            <div className="grid gap-3 sm:grid-cols-2">
              <Field label="Plex URL" value={settings.plex_url ?? ""} onChange={(v) => set("plex_url", v)} placeholder="http://192.168.1.100:32400" />
              <div>
                <Field label="Plex token" type="password" value={settings.plex_token ?? ""} onChange={(v) => set("plex_token", v)} placeholder="Your X-Plex-Token" />
                <p className="mt-1 text-xs text-gray-500 dark:text-gray-400">
                  <a href="https://support.plex.tv/articles/204059436" target="_blank" rel="noopener noreferrer" className="text-blue-600 dark:text-blue-400 underline">
                    How to find your Plex token
                  </a>
                </p>
              </div>
            </div>
            <div>
              <label className="mb-1 block text-sm font-medium text-gray-700 dark:text-gray-300">Library to refresh</label>
              <select value={settings.plex_library_key ?? ""} onChange={(e) => set("plex_library_key", e.target.value)} className={FIELD}>
                <option value="">All libraries</option>
                {plexLibraries.map((lib) => (
                  <option key={lib.key} value={lib.key}>{lib.title} ({lib.type})</option>
                ))}
              </select>
              {plexLibraries.length === 0 && (
                <p className="mt-1 text-xs text-gray-400 dark:text-gray-500">Click "Test connection" to load available libraries.</p>
              )}
            </div>
            <div className="flex items-center gap-3">
              <button onClick={handleTestPlex} disabled={testingPlex}
                className="flex items-center gap-1.5 rounded-lg border border-gray-300 dark:border-gray-600 px-4 py-2 text-sm font-medium text-gray-700 dark:text-gray-300 hover:bg-gray-100 dark:hover:bg-gray-800 disabled:opacity-50">
                {testingPlex && <Loader2 className="h-4 w-4 animate-spin" />}
                Test connection
              </button>
              {plexTestResult && (
                <span className={`flex items-center gap-1.5 text-sm font-medium ${plexTestResult.ok ? "text-green-600 dark:text-green-400" : "text-red-600 dark:text-red-400"}`}>
                  {plexTestResult.ok ? <CheckCircle2 className="h-4 w-4" /> : <AlertCircle className="h-4 w-4" />}
                  {plexTestResult.ok
                    ? `${plexLibraries.length} librar${plexLibraries.length === 1 ? "y" : "ies"} found`
                    : plexTestResult.error}
                </span>
              )}
            </div>
          </div>
        </Section>

        {/* Jellyfin */}
        <Section title="Jellyfin">
          <p className="mb-4 text-xs text-gray-500 dark:text-gray-400">
            Optional. AutoRrent will refresh your Jellyfin library whenever a download completes.
          </p>
          <div className="mb-4 border-b border-gray-100 dark:border-gray-800 pb-4">
            <label className="flex cursor-pointer items-start gap-3">
              <input
                type="checkbox"
                checked={settings.jellyfin_enabled === "true"}
                onChange={(e) => set("jellyfin_enabled", e.target.checked ? "true" : "false")}
                className="mt-0.5 h-4 w-4 rounded border-gray-300 text-blue-600"
              />
              <span className="text-sm font-medium text-gray-700 dark:text-gray-300">Enable Jellyfin library refresh</span>
            </label>
          </div>
          <div className={`space-y-3 ${settings.jellyfin_enabled !== "true" ? "opacity-50 pointer-events-none" : ""}`}>
            <div className="grid gap-3 sm:grid-cols-2">
              <Field label="Jellyfin URL" value={settings.jellyfin_url ?? ""} onChange={(v) => set("jellyfin_url", v)} placeholder="http://192.168.1.100:8096" />
              <Field label="API key" type="password" value={settings.jellyfin_api_key ?? ""} onChange={(v) => set("jellyfin_api_key", v)} placeholder="Your API key" />
            </div>
            <Field
              label="Library ID"
              value={settings.jellyfin_library_id ?? ""}
              onChange={(v) => set("jellyfin_library_id", v)}
              placeholder="Leave blank to refresh all libraries"
              hint="The ItemId of a specific library, or leave blank to refresh everything."
            />
            <div className="flex items-center gap-3">
              <button onClick={handleTestJellyfin} disabled={testingJellyfin}
                className="flex items-center gap-1.5 rounded-lg border border-gray-300 dark:border-gray-600 px-4 py-2 text-sm font-medium text-gray-700 dark:text-gray-300 hover:bg-gray-100 dark:hover:bg-gray-800 disabled:opacity-50">
                {testingJellyfin && <Loader2 className="h-4 w-4 animate-spin" />}
                Test connection
              </button>
              {jellyfinTestResult && (
                <span className={`flex items-center gap-1.5 text-sm font-medium ${jellyfinTestResult.ok ? "text-green-600 dark:text-green-400" : "text-red-600 dark:text-red-400"}`}>
                  {jellyfinTestResult.ok ? <CheckCircle2 className="h-4 w-4" /> : <AlertCircle className="h-4 w-4" />}
                  {jellyfinTestResult.ok
                    ? `${jellyfinTestResult.server_name} v${jellyfinTestResult.version}`
                    : jellyfinTestResult.error}
                </span>
              )}
            </div>
          </div>
        </Section>

        {/* Notifications */}
        <Section title="Notifications">
          <p className="mb-3 text-xs text-gray-500 dark:text-gray-400">
            Uses{" "}
            <a href="https://github.com/caronc/apprise" target="_blank" rel="noopener noreferrer" className="text-blue-600 dark:text-blue-400 underline">
              Apprise
            </a>{" "}
            — supports Telegram, Discord, Slack, email, Pushover, and 60+ more. Leave blank to disable.
          </p>
          <Field label="Apprise URL" value={settings.apprise_url ?? ""} onChange={(v) => set("apprise_url", v)} placeholder="tgram://bottoken/ChatID" />
        </Section>

        {/* Save */}
        <div className="flex justify-end">
          <button onClick={handleSave} disabled={saving}
            className="flex items-center gap-2 rounded-lg bg-blue-600 px-6 py-2.5 text-sm font-medium text-white hover:bg-blue-700 disabled:opacity-60">
            {saving ? <Loader2 className="h-4 w-4 animate-spin" /> : <Save className="h-4 w-4" />}
            Save settings
          </button>
        </div>
      </div>

      {pathModal !== null && (
        <PathModal initial={pathModal === true ? undefined : pathModal} onClose={() => setPathModal(null)} onSave={handlePathSave} />
      )}

      {toast && (
        <div className="fixed bottom-6 right-6 z-50 rounded-lg bg-gray-900 dark:bg-gray-700 px-4 py-3 text-sm text-white shadow-lg">
          {toast}
        </div>
      )}
    </div>
  );
}
