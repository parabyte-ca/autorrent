import { useEffect, useState } from "react";
import { AlertCircle, CheckCircle2, Download, Edit2, FolderOpen, Loader2, Plus, Save, Star, Trash2, Upload } from "lucide-react";
import { api, type DownloadPath, type JackettTestResult, type TraktResult, type TraktStatus, type JellyfinTestResult, type ProfileInput, type QualityProfile, type PlexLibrary, type PlexTestResult, type RestoreResponse, type Settings as SettingsType } from "../api/client";
import { authStore } from "../auth";

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

function Field({ label, hint, type = "text", value, onChange, placeholder, autoComplete }: {
  label: string; hint?: string; type?: string;
  value: string; onChange: (v: string) => void; placeholder?: string;
  autoComplete?: string;
}) {
  return (
    <div>
      <label className="mb-1 block text-sm font-medium text-gray-700 dark:text-gray-300">{label}</label>
      {hint && <p className="mb-1 text-xs text-gray-500 dark:text-gray-400">{hint}</p>}
      <input
        type={type}
        value={value}
        onChange={(e) => onChange(e.target.value)}
        placeholder={placeholder}
        autoComplete={autoComplete ?? (type === "password" ? "new-password" : undefined)}
        className={FIELD}
      />
    </div>
  );
}

function CheckRow({ label, hint, checked, onChange }: {
  label: string; hint?: string; checked: boolean; onChange: (v: boolean) => void;
}) {
  return (
    <label className="flex cursor-pointer items-start gap-3">
      <input
        type="checkbox"
        checked={checked}
        onChange={(e) => onChange(e.target.checked)}
        className="mt-0.5 h-4 w-4 rounded border-gray-300 text-blue-600"
      />
      <div>
        <span className="text-sm font-medium text-gray-700 dark:text-gray-300">{label}</span>
        {hint && <p className="mt-0.5 text-xs text-gray-500 dark:text-gray-400">{hint}</p>}
      </div>
    </label>
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

function describeTrakt(r: TraktResult): string {
  const parts = [`${r.checked} on your Trakt watchlist`];
  parts.push(r.added.length ? `added ${r.added.length} (${r.added.join(", ")})` : "nothing new to add");
  if (r.existing) parts.push(`${r.existing} already tracked`);
  if (r.unmatched.length) parts.push(`${r.unmatched.length} couldn't be matched on TVMaze (${r.unmatched.join(", ")})`);
  if (r.deferred) parts.push(`${r.deferred} more waiting for the next sync`);
  return parts.join(" · ");
}

// ── Quality profiles ──────────────────────────────────────────────────────────

const PROFILE_QUALITIES = ["4K", "1080p", "720p", "480p", "Any"];
const PROFILE_CODECS = ["x265", "x264", "AV1", "Any"];

const EMPTY_PROFILE: ProfileInput = {
  name: "", quality: "1080p", codec: "x265", max_size_mb: null, preferred_groups: null,
  prefer_season_packs: false, fallback_quality: null, fallback_after_hours: 24,
};

function ProfileModal({ initial, onClose, onSave }: {
  initial?: QualityProfile;
  onClose: () => void;
  onSave: (data: ProfileInput) => Promise<void>;
}) {
  const [form, setForm] = useState<ProfileInput>(
    initial
      ? {
          name: initial.name, quality: initial.quality, codec: initial.codec,
          max_size_mb: initial.max_size_mb, preferred_groups: initial.preferred_groups,
          prefer_season_packs: initial.prefer_season_packs,
          fallback_quality: initial.fallback_quality, fallback_after_hours: initial.fallback_after_hours,
        }
      : EMPTY_PROFILE,
  );
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const set = <K extends keyof ProfileInput>(k: K, v: ProfileInput[K]) => setForm((f) => ({ ...f, [k]: v }));

  const submit = async () => {
    setSaving(true);
    setError(null);
    try {
      await onSave({ ...form, name: form.name.trim() });
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not save profile.");
      setSaving(false);
    }
  };

  const label = "mb-1 block text-sm font-medium text-gray-700 dark:text-gray-300";
  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/50 p-4">
      <div className="w-full max-w-md rounded-xl bg-white dark:bg-gray-900 border border-gray-200 dark:border-gray-700 p-6 shadow-xl max-h-[90vh] overflow-y-auto">
        <h2 className="mb-4 text-lg font-semibold text-gray-900 dark:text-gray-100">
          {initial ? "Edit profile" : "New quality profile"}
        </h2>
        <div className="space-y-3">
          <Field label="Name" value={form.name} onChange={(v) => set("name", v)} placeholder="e.g. Prestige 4K" />
          <div className="flex gap-3">
            <div className="flex-1">
              <label className={label}>Quality</label>
              <select value={form.quality} onChange={(e) => set("quality", e.target.value)} className={FIELD}>
                {PROFILE_QUALITIES.map((q) => <option key={q}>{q}</option>)}
              </select>
            </div>
            <div className="flex-1">
              <label className={label}>Codec</label>
              <select value={form.codec} onChange={(e) => set("codec", e.target.value)} className={FIELD}>
                {PROFILE_CODECS.map((c) => <option key={c}>{c}</option>)}
              </select>
            </div>
          </div>
          <div>
            <label className={label}>Max size (MB) <span className="text-xs font-normal text-gray-400">optional</span></label>
            <input type="number" min={1} value={form.max_size_mb ?? ""} placeholder="e.g. 4000"
              onChange={(e) => set("max_size_mb", e.target.value ? Number(e.target.value) : null)} className={FIELD} />
          </div>
          <Field label="Preferred release groups" hint="Comma-separated, in priority order." value={form.preferred_groups ?? ""}
            onChange={(v) => set("preferred_groups", v || null)} placeholder="e.g. FLUX, NTb" />
          <CheckRow label="Prefer season packs"
            hint="Grab a whole season at once when the season has finished airing."
            checked={form.prefer_season_packs} onChange={(v) => set("prefer_season_packs", v)} />
          <div className="rounded-lg border border-gray-100 dark:border-gray-800 p-3">
            <label className={label}>Fall back to</label>
            <p className="mb-1 text-xs text-gray-500 dark:text-gray-400">
              If nothing is found at the quality above, settle for this once the episode has been out a while.
            </p>
            <div className="flex gap-3">
              <select value={form.fallback_quality ?? ""} className={FIELD}
                onChange={(e) => set("fallback_quality", e.target.value || null)}>
                <option value="">No fallback</option>
                {PROFILE_QUALITIES.filter((q) => q !== form.quality).map((q) => <option key={q}>{q}</option>)}
              </select>
              {form.fallback_quality && (
                <div className="flex w-40 shrink-0 items-center gap-2 text-sm text-gray-600 dark:text-gray-400">
                  after
                  <input type="number" min={1} value={form.fallback_after_hours} className={FIELD}
                    onChange={(e) => set("fallback_after_hours", Math.max(1, Number(e.target.value) || 1))} />
                  h
                </div>
              )}
            </div>
          </div>
        </div>
        {error && <p className="mt-3 text-sm text-red-600 dark:text-red-400">{error}</p>}
        <div className="mt-5 flex gap-3">
          <button onClick={onClose} className="flex-1 rounded-lg border border-gray-300 dark:border-gray-600 px-4 py-2 text-sm font-medium text-gray-700 dark:text-gray-300 hover:bg-gray-50 dark:hover:bg-gray-800">
            Cancel
          </button>
          <button onClick={submit} disabled={!form.name.trim() || saving}
            className="flex-1 rounded-lg bg-blue-600 px-4 py-2 text-sm font-medium text-white hover:bg-blue-700 disabled:opacity-50">
            {saving ? "Saving…" : initial ? "Save changes" : "Create profile"}
          </button>
        </div>
      </div>
    </div>
  );
}

function ProfilesSection({ defaultId, onDefaultChange }: {
  defaultId: string;
  onDefaultChange: (id: string) => void;
}) {
  const [profiles, setProfiles] = useState<QualityProfile[] | null>(null);
  const [modal, setModal] = useState<QualityProfile | true | null>(null);
  const [error, setError] = useState<string | null>(null);

  const load = () => api.profiles.list().then(setProfiles).catch(() => setProfiles([]));
  useEffect(() => { load(); }, []);

  const save = async (data: ProfileInput) => {
    if (modal === true) await api.profiles.create(data);
    else if (modal) await api.profiles.update(modal.id, data);
    setModal(null);
    load();
  };

  const remove = async (p: QualityProfile) => {
    const used = p.watchlist_count
      ? ` ${p.watchlist_count} show${p.watchlist_count === 1 ? "" : "s"} use it and will keep its settings as their own.`
      : "";
    if (!confirm(`Delete profile "${p.name}"?${used}`)) return;
    try {
      await api.profiles.delete(p.id);
      if (String(p.id) === defaultId) onDefaultChange("");
      load();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not delete profile.");
    }
  };

  return (
    <>
      <p className="mb-4 text-xs text-gray-500 dark:text-gray-400">
        Reusable download preferences — for example "4K, fall back to 1080p after a day" — that you can assign to
        any show. Editing a profile updates every show using it.
      </p>
      <div className="mb-3 flex items-end justify-between gap-3">
        <div className="min-w-0 flex-1 sm:max-w-xs">
          <label className="mb-1 block text-sm font-medium text-gray-700 dark:text-gray-300">Default for new shows</label>
          <select value={defaultId} onChange={(e) => onDefaultChange(e.target.value)} className={FIELD}>
            <option value="">None (custom settings)</option>
            {(profiles ?? []).map((p) => <option key={p.id} value={p.id}>{p.name}</option>)}
          </select>
        </div>
        <button onClick={() => setModal(true)}
          className="flex items-center gap-1.5 rounded-lg bg-blue-600 px-3 py-1.5 text-sm font-medium text-white hover:bg-blue-700">
          <Plus className="h-4 w-4" /> Add profile
        </button>
      </div>
      {error && <p className="mb-2 text-sm text-red-600 dark:text-red-400">{error}</p>}
      {profiles === null ? (
        <Loader2 className="mx-auto h-5 w-5 animate-spin text-gray-400" />
      ) : profiles.length === 0 ? (
        <div className="rounded-lg border-2 border-dashed border-gray-200 dark:border-gray-700 py-6 text-center text-sm text-gray-400 dark:text-gray-600">
          No profiles yet.
        </div>
      ) : (
        <ul className="divide-y divide-gray-100 dark:divide-gray-800">
          {profiles.map((p) => (
            <li key={p.id} className="flex items-center gap-3 py-2.5">
              <div className="min-w-0 flex-1">
                <p className="text-sm font-medium text-gray-900 dark:text-gray-100">{p.name}</p>
                <p className="text-xs text-gray-500 dark:text-gray-400">
                  {[p.quality, p.codec,
                    p.max_size_mb ? `max ${p.max_size_mb} MB` : null,
                    p.prefer_season_packs ? "season packs" : null,
                    p.fallback_quality ? `→ ${p.fallback_quality} after ${p.fallback_after_hours}h` : null,
                  ].filter(Boolean).join(" · ")}
                  {" · "}used by {p.watchlist_count} show{p.watchlist_count === 1 ? "" : "s"}
                </p>
              </div>
              <button onClick={() => setModal(p)} className="rounded-lg p-1.5 text-gray-400 hover:bg-gray-100 dark:hover:bg-gray-800">
                <Edit2 className="h-3.5 w-3.5" />
              </button>
              <button onClick={() => remove(p)} className="rounded-lg p-1.5 text-gray-400 hover:bg-red-50 dark:hover:bg-red-950 hover:text-red-500">
                <Trash2 className="h-3.5 w-3.5" />
              </button>
            </li>
          ))}
        </ul>
      )}
      {modal !== null && (
        <ProfileModal initial={modal === true ? undefined : modal} onClose={() => setModal(null)} onSave={save} />
      )}
    </>
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
  const [testingDigest,    setTestingDigest]    = useState(false);
  const [digestTestResult, setDigestTestResult] = useState<{ ok: boolean; message?: string; error?: string } | null>(null);
  const [health,           setHealth]           = useState<HealthStatus>(null);
  const [exportingBackup,  setExportingBackup]  = useState(false);
  const [exportError,      setExportError]      = useState<string | null>(null);
  const [backupFile,       setBackupFile]       = useState<File | null>(null);
  const [restoringBackup,  setRestoringBackup]  = useState(false);
  const [restoreResult,    setRestoreResult]    = useState<RestoreResponse | null>(null);

  const showToast = (msg: string) => { setToast(msg); setTimeout(() => setToast(null), 3000); };

  const handleRevokeAllSessions = async () => {
    if (!confirm("This will sign out all active sessions. Continue?")) return;
    try {
      await api.auth.revoke();
      authStore.clearToken();
      showToast("All sessions revoked.");
    } catch (e: unknown) {
      showToast(`Error: ${(e as Error).message}`);
    }
  };

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

  const [trakt, setTrakt] = useState<TraktStatus | null>(null);
  const [traktBusy, setTraktBusy] = useState<"test" | "sync" | "forget" | null>(null);
  const [traktMsg, setTraktMsg] = useState<{ ok: boolean; text: string } | null>(null);
  useEffect(() => { api.trakt.status().then(setTrakt).catch(() => {}); }, []);

  const saveTraktFields = () => api.settings.update({
    trakt_enabled: settings.trakt_enabled ?? "false",
    trakt_client_id: (settings.trakt_client_id ?? "").trim(),
    trakt_username: (settings.trakt_username ?? "").trim(),
    trakt_start: settings.trakt_start ?? "auto",
  });
  const testTrakt = async () => {
    setTraktBusy("test");
    setTraktMsg(null);
    try {
      const r = await api.trakt.test({
        client_id: (settings.trakt_client_id ?? "").trim(), username: (settings.trakt_username ?? "").trim(),
      });
      setTraktMsg(r.ok
        ? { ok: true, text: `Connected — ${r.shows} show${r.shows === 1 ? "" : "s"} on the watchlist.` }
        : { ok: false, text: r.error ?? "Test failed." });
    } catch (err) {
      setTraktMsg({ ok: false, text: err instanceof Error ? err.message : "Test failed." });
    } finally { setTraktBusy(null); }
  };
  const syncTrakt = async () => {
    setTraktBusy("sync");
    setTraktMsg(null);
    try {
      await saveTraktFields();   // the sync reads saved settings
      const r = await api.trakt.sync();
      setTraktMsg(r.ok ? { ok: true, text: describeTrakt(r) } : { ok: false, text: r.error ?? "Sync failed." });
      setTrakt(await api.trakt.status());
    } catch (err) {
      setTraktMsg({ ok: false, text: err instanceof Error ? err.message : "Sync failed." });
    } finally { setTraktBusy(null); }
  };
  const forgetTrakt = async () => {
    if (!confirm("Forget which Trakt shows were already handled? The next sync will consider every show on your watchlist again, including ones you removed from AutoRrent.")) return;
    setTraktBusy("forget");
    try {
      await api.trakt.forgetHistory();
      setTrakt(await api.trakt.status());
      setTraktMsg({ ok: true, text: "Sync history cleared." });
    } finally { setTraktBusy(null); }
  };

  const [jackettTesting, setJackettTesting] = useState(false);
  const [jackettResult, setJackettResult] = useState<JackettTestResult | null>(null);
  const testJackett = async () => {
    setJackettTesting(true);
    setJackettResult(null);
    try {
      setJackettResult(await api.settings.testJackett({
        url: settings.jackett_url ?? "", api_key: settings.jackett_api_key ?? "",
      }));
    } catch (err) {
      setJackettResult({ ok: false, error: err instanceof Error ? err.message : "Test failed." });
    } finally {
      setJackettTesting(false);
    }
  };

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

  const handleTestDigest = async () => {
    setTestingDigest(true); setDigestTestResult(null);
    try {
      setDigestTestResult(await api.settings.testDigest({
        digest_smtp_host:     settings.digest_smtp_host ?? "",
        digest_smtp_port:     settings.digest_smtp_port ?? "587",
        digest_smtp_user:     settings.digest_smtp_user ?? "",
        digest_smtp_password: settings.digest_smtp_password ?? "",
        digest_from_email:    settings.digest_from_email ?? "",
        digest_recipients:    settings.digest_recipients ?? "",
        plex_url:             settings.plex_url ?? "",
        plex_token:           settings.plex_token ?? "",
        digest_excluded_libs: settings.digest_excluded_libs ?? "",
      }));
    } finally { setTestingDigest(false); }
  };

  const handleExportBackup = async () => {
    setExportingBackup(true);
    setExportError(null);
    try {
      const res = await api.backup.export();
      if (!res.ok) {
        const err = await res.json().catch(() => ({ detail: "Export failed" }));
        setExportError(err.detail ?? "Export failed");
        return;
      }
      const blob = await res.blob();
      const cd = res.headers.get("Content-Disposition") ?? "";
      const match = cd.match(/filename="([^"]+)"/);
      const filename = match?.[1] ?? `autorrent-backup-${new Date().toISOString().slice(0, 10)}.zip`;
      const url = URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = url;
      a.download = filename;
      document.body.appendChild(a);
      a.click();
      document.body.removeChild(a);
      URL.revokeObjectURL(url);
    } catch (e: unknown) {
      setExportError((e as Error).message);
    } finally {
      setExportingBackup(false);
    }
  };

  const handleRestoreBackup = async () => {
    if (!backupFile) return;
    if (!confirm("This will replace all current settings and data with the contents of the backup. Are you sure?")) return;
    setRestoringBackup(true);
    setRestoreResult(null);
    try {
      const res = await api.backup.restore(backupFile);
      const body: RestoreResponse = await res.json().catch(() => ({ ok: false, error: "Unexpected response from server" }));
      setRestoreResult(body);
      if (body.ok) {
        setTimeout(() => window.location.reload(), 2000);
      }
    } catch (e: unknown) {
      setRestoreResult({ ok: false, error: (e as Error).message });
    } finally {
      setRestoringBackup(false);
    }
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
    <div className="px-4 py-6 sm:px-6 max-w-2xl">
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
                    {p.free_bytes != null && (
                      <p className={`text-xs ${p.low_space ? "font-medium text-amber-600 dark:text-amber-400" : "text-gray-400 dark:text-gray-500"}`}>
                        {(p.free_bytes / 1e9).toFixed(1)} GB free{p.low_space ? " — low space" : ""}
                      </p>
                    )}
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
          <div className="mt-4 border-t border-gray-100 dark:border-gray-800 pt-4 sm:max-w-xs">
            <Field
              label="Low disk-space warning (GB)"
              hint="Alert and show a banner when a folder has less free space than this. 0 disables."
              type="number"
              value={settings.low_disk_warn_gb ?? "10"}
              onChange={(v) => set("low_disk_warn_gb", v)}
            />
          </div>
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

          <div className="mt-4 border-t border-gray-100 dark:border-gray-800 pt-4">
            <CheckRow
              label="Automatically retry failed or stalled downloads"
              hint="When a watchlist download errors out or makes no progress, remove it and grab the next-best release for that episode."
              checked={(settings.retry_enabled ?? "true") === "true"}
              onChange={(v) => set("retry_enabled", v ? "true" : "false")}
            />
            <div className="mt-3 grid gap-3 sm:grid-cols-2">
              <Field
                label="Stall after (hours)"
                hint="No download progress for this long counts as stalled."
                type="number"
                value={settings.stall_hours ?? "2"}
                onChange={(v) => set("stall_hours", v)}
              />
              <Field
                label="Max attempts per episode"
                hint="Including the first grab. After this AutoRrent gives up and alerts you."
                type="number"
                value={settings.retry_max_attempts ?? "3"}
                onChange={(v) => set("retry_max_attempts", v)}
              />
            </div>
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
          <div className="mt-3 flex flex-wrap items-center gap-3">
            <button
              onClick={testJackett}
              disabled={jackettTesting}
              className="flex items-center gap-1.5 rounded-lg border border-gray-300 dark:border-gray-600 px-3 py-1.5 text-sm font-medium text-gray-700 dark:text-gray-300 hover:bg-gray-50 dark:hover:bg-gray-800 disabled:opacity-50"
            >
              {jackettTesting && <Loader2 className="h-3.5 w-3.5 animate-spin" />}
              Test connection
            </button>
            {jackettResult && (
              <span className={`flex items-center gap-1 text-sm ${jackettResult.ok ? "text-green-600 dark:text-green-400" : "text-red-600 dark:text-red-400"}`}>
                {jackettResult.ok ? <CheckCircle2 className="h-4 w-4" /> : <AlertCircle className="h-4 w-4" />}
                {jackettResult.ok
                  ? `Connected to ${jackettResult.kind}${jackettResult.version ? ` ${jackettResult.version}` : ""} — ${jackettResult.indexers} indexer${jackettResult.indexers === 1 ? "" : "s"} enabled`
                  : jackettResult.error}
              </span>
            )}
          </div>
        </Section>

        {/* Quality profiles */}
        <Section title="Quality Profiles">
          <ProfilesSection
            defaultId={settings.default_profile_id ?? ""}
            onDefaultChange={(id) => set("default_profile_id", id)}
          />
        </Section>

        {/* Trakt */}
        <Section title="Trakt Watchlist Sync">
          <p className="mb-3 text-xs text-gray-500 dark:text-gray-400">
            Shows you add to your Trakt watchlist are added to AutoRrent automatically (checked every 6 hours).
            Trakt has no push notifications for this, so AutoRrent polls it. You need a free API app:
            create one at <span className="font-mono">trakt.tv/oauth/applications</span> (any name; redirect URI{" "}
            <span className="font-mono">urn:ietf:wg:oauth:2.0:oob</span>) and paste its <em>Client ID</em> below.
            Your watchlist must be public (Trakt → Settings → Privacy).
          </p>
          <CheckRow
            label="Sync my Trakt watchlist automatically"
            checked={(settings.trakt_enabled ?? "false") === "true"}
            onChange={(v) => set("trakt_enabled", v ? "true" : "false")}
          />
          <div className="mt-3 grid gap-3 sm:grid-cols-2">
            <Field label="Client ID" value={settings.trakt_client_id ?? ""} onChange={(v) => set("trakt_client_id", v)} placeholder="64-character Trakt client ID" autoComplete="off" />
            <Field label="Trakt username" value={settings.trakt_username ?? ""} onChange={(v) => set("trakt_username", v)} placeholder="your-username" autoComplete="off" />
          </div>
          <div className="mt-3 sm:max-w-xs">
            <label className="mb-1 block text-sm font-medium text-gray-700 dark:text-gray-300">New shows start from</label>
            <select value={settings.trakt_start ?? "auto"} onChange={(e) => set("trakt_start", e.target.value)} className={FIELD}>
              <option value="auto">Automatic</option>
              <option value="next">Next new episode</option>
              <option value="beginning">The beginning</option>
            </select>
            <p className="mt-1 text-xs text-gray-500 dark:text-gray-400">
              Automatic starts at the next episode for shows still airing, and at the beginning for finished ones.
            </p>
          </div>
          <p className="mt-2 text-xs text-gray-500 dark:text-gray-400">
            To avoid flooding your downloads, at most {trakt?.max_new_per_run ?? 10} new shows are added per sync; the rest follow on the next one.
          </p>
          <div className="mt-3 flex flex-wrap items-center gap-2">
            <button onClick={testTrakt} disabled={traktBusy !== null}
              className="flex items-center gap-1.5 rounded-lg border border-gray-300 dark:border-gray-600 px-3 py-1.5 text-sm font-medium text-gray-700 dark:text-gray-300 hover:bg-gray-50 dark:hover:bg-gray-800 disabled:opacity-50">
              {traktBusy === "test" && <Loader2 className="h-3.5 w-3.5 animate-spin" />}Test connection
            </button>
            <button onClick={syncTrakt} disabled={traktBusy !== null}
              className="flex items-center gap-1.5 rounded-lg border border-gray-300 dark:border-gray-600 px-3 py-1.5 text-sm font-medium text-gray-700 dark:text-gray-300 hover:bg-gray-50 dark:hover:bg-gray-800 disabled:opacity-50">
              {traktBusy === "sync" && <Loader2 className="h-3.5 w-3.5 animate-spin" />}Sync now
            </button>
            {trakt && trakt.seen_count > 0 && (
              <button onClick={forgetTrakt} disabled={traktBusy !== null}
                className="rounded-lg px-3 py-1.5 text-sm text-gray-500 dark:text-gray-400 hover:underline disabled:opacity-50">
                Forget sync history ({trakt.seen_count})
              </button>
            )}
          </div>
          {traktMsg && (
            <p className={`mt-2 flex items-start gap-1 text-sm ${traktMsg.ok ? "text-green-600 dark:text-green-400" : "text-red-600 dark:text-red-400"}`}>
              {traktMsg.ok ? <CheckCircle2 className="mt-0.5 h-4 w-4 shrink-0" /> : <AlertCircle className="mt-0.5 h-4 w-4 shrink-0" />}
              <span>{traktMsg.text}</span>
            </p>
          )}
          {!traktMsg && trakt?.last_result && (
            <p className="mt-2 text-xs text-gray-500 dark:text-gray-400">
              Last sync {new Date(trakt.last_result.at).toLocaleString()}:{" "}
              {trakt.last_result.ok ? describeTrakt(trakt.last_result) : trakt.last_result.error}
            </p>
          )}
        </Section>

        {/* Discovery */}
        <Section title="Discovery">
          <p className="mb-3 text-xs text-gray-500 dark:text-gray-400">
            The Browse page lists popular shows airing in this country, using free TVMaze schedules.
          </p>
          <div className="sm:max-w-xs">
            <Field
              label="Country code"
              hint="Two letters, e.g. US, CA, GB, AU."
              value={settings.browse_country ?? "US"}
              onChange={(v) => set("browse_country", v.toUpperCase().slice(0, 2))}
              placeholder="US"
            />
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

          <div className="mt-4 space-y-3 border-t border-gray-100 dark:border-gray-800 pt-4">
            <p className="text-sm font-medium text-gray-700 dark:text-gray-300">Send an alert when…</p>
            <CheckRow
              label="A watchlist episode is grabbed"
              checked={(settings.notify_on_grab ?? "true") === "true"}
              onChange={(v) => set("notify_on_grab", v ? "true" : "false")}
            />
            <CheckRow
              label="A download completes"
              checked={(settings.notify_on_complete ?? "true") === "true"}
              onChange={(v) => set("notify_on_complete", v ? "true" : "false")}
            />
            <CheckRow
              label="A download fails"
              checked={(settings.notify_on_failed ?? "true") === "true"}
              onChange={(v) => set("notify_on_failed", v ? "true" : "false")}
            />
            <CheckRow
              label="A download stalls"
              checked={(settings.notify_on_stalled ?? "true") === "true"}
              onChange={(v) => set("notify_on_stalled", v ? "true" : "false")}
            />
          </div>
        </Section>

        {/* Weekly Digest */}
        <Section title="Weekly Digest Email">
          <p className="mb-4 text-xs text-gray-500 dark:text-gray-400">
            Send a weekly email listing new Movies and TV Shows added to Plex, split into regular and mature sections by content rating.
          </p>
          <div className="mb-4 border-b border-gray-100 dark:border-gray-800 pb-4">
            <label className="flex cursor-pointer items-start gap-3">
              <input
                type="checkbox"
                checked={settings.digest_enabled === "true"}
                onChange={(e) => set("digest_enabled", e.target.checked ? "true" : "false")}
                className="mt-0.5 h-4 w-4 rounded border-gray-300 text-blue-600"
              />
              <span className="text-sm font-medium text-gray-700 dark:text-gray-300">Enable weekly digest</span>
            </label>
          </div>

          <div className={`space-y-4 ${settings.digest_enabled !== "true" ? "opacity-50 pointer-events-none" : ""}`}>
            {/* SMTP */}
            <div>
              <h3 className="mb-3 text-sm font-semibold text-gray-700 dark:text-gray-300">SMTP Configuration</h3>
              <div className="grid gap-3 sm:grid-cols-2">
                <Field label="SMTP Host" value={settings.digest_smtp_host ?? ""} onChange={(v) => set("digest_smtp_host", v)} placeholder="smtp.gmail.com" />
                <Field label="Port" value={settings.digest_smtp_port ?? "587"} onChange={(v) => set("digest_smtp_port", v)} placeholder="587" />
                <Field label="Username" value={settings.digest_smtp_user ?? ""} onChange={(v) => set("digest_smtp_user", v)} placeholder="you@gmail.com" />
                <Field label="Password" type="password" value={settings.digest_smtp_password ?? ""} onChange={(v) => set("digest_smtp_password", v)} placeholder="••••••••" />
                <div className="sm:col-span-2">
                  <Field
                    label="From address"
                    value={settings.digest_from_email ?? ""}
                    onChange={(v) => set("digest_from_email", v)}
                    placeholder="AutoRrent <you@gmail.com>"
                    hint="Defaults to the username above if left blank."
                  />
                </div>
              </div>
            </div>

            {/* Recipients */}
            <div>
              <label className="mb-1 block text-sm font-medium text-gray-700 dark:text-gray-300">Recipients</label>
              <p className="mb-1 text-xs text-gray-500 dark:text-gray-400">One email address per line, or comma-separated.</p>
              <textarea
                rows={3}
                value={settings.digest_recipients ?? ""}
                onChange={(e) => set("digest_recipients", e.target.value)}
                placeholder={"alice@example.com\nbob@example.com"}
                className={`${FIELD} resize-y`}
              />
            </div>

            {/* Schedule */}
            <div>
              <h3 className="mb-3 text-sm font-semibold text-gray-700 dark:text-gray-300">Schedule (UTC)</h3>
              <div className="grid gap-3 sm:grid-cols-2">
                <div>
                  <label className="mb-1 block text-sm font-medium text-gray-700 dark:text-gray-300">Day of week</label>
                  <select value={settings.digest_day_of_week ?? "mon"} onChange={(e) => set("digest_day_of_week", e.target.value)} className={FIELD}>
                    {([["mon","Monday"],["tue","Tuesday"],["wed","Wednesday"],["thu","Thursday"],["fri","Friday"],["sat","Saturday"],["sun","Sunday"]] as [string,string][]).map(([v,l]) => (
                      <option key={v} value={v}>{l}</option>
                    ))}
                  </select>
                </div>
                <div>
                  <label className="mb-1 block text-sm font-medium text-gray-700 dark:text-gray-300">Hour (UTC)</label>
                  <select value={settings.digest_hour ?? "8"} onChange={(e) => set("digest_hour", e.target.value)} className={FIELD}>
                    {Array.from({length: 24}, (_, i) => (
                      <option key={i} value={String(i)}>{String(i).padStart(2,"0")}:00 UTC</option>
                    ))}
                  </select>
                </div>
              </div>
            </div>

            {/* Excluded libraries */}
            <div>
              <Field
                label="Excluded libraries"
                value={settings.digest_excluded_libs ?? ""}
                onChange={(v) => set("digest_excluded_libs", v)}
                placeholder="bellesa, adult-content"
                hint="Comma-separated Plex library names to exclude from the digest (case-insensitive)."
              />
            </div>

            {/* Test button */}
            <div className="flex items-center gap-3 border-t border-gray-100 dark:border-gray-800 pt-4">
              <button
                onClick={handleTestDigest}
                disabled={testingDigest}
                className="flex items-center gap-1.5 rounded-lg border border-gray-300 dark:border-gray-600 px-4 py-2 text-sm font-medium text-gray-700 dark:text-gray-300 hover:bg-gray-100 dark:hover:bg-gray-800 disabled:opacity-50"
              >
                {testingDigest && <Loader2 className="h-4 w-4 animate-spin" />}
                Send test digest now
              </button>
              {digestTestResult && (
                <span className={`flex items-center gap-1.5 text-sm font-medium ${digestTestResult.ok ? "text-green-600 dark:text-green-400" : "text-red-600 dark:text-red-400"}`}>
                  {digestTestResult.ok ? <CheckCircle2 className="h-4 w-4" /> : <AlertCircle className="h-4 w-4" />}
                  {digestTestResult.ok ? digestTestResult.message : digestTestResult.error}
                </span>
              )}
            </div>
          </div>
        </Section>

        {/* Data management */}
        <Section title="Data management">
          {/* Export */}
          <div className="space-y-3">
            <div>
              <h3 className="text-sm font-medium text-gray-700 dark:text-gray-300">Export backup</h3>
              <p className="mt-0.5 text-xs text-gray-500 dark:text-gray-400">
                Download a ZIP file containing your database and settings. Use this before upgrading or migrating to a new server.
              </p>
            </div>
            <button
              onClick={handleExportBackup}
              disabled={exportingBackup}
              className="flex items-center gap-1.5 rounded-lg border border-gray-300 dark:border-gray-600 px-4 py-2 text-sm font-medium text-gray-700 dark:text-gray-300 hover:bg-gray-100 dark:hover:bg-gray-800 disabled:opacity-60"
            >
              {exportingBackup ? <Loader2 className="h-4 w-4 animate-spin" /> : <Download className="h-4 w-4" />}
              Download backup
            </button>
            {exportError && (
              <div className="flex items-center gap-2 rounded-lg border border-red-200 dark:border-red-900 bg-red-50 dark:bg-red-950 px-3 py-2 text-sm text-red-700 dark:text-red-400">
                <AlertCircle className="h-4 w-4 shrink-0" />
                {exportError}
              </div>
            )}
          </div>

          <div className="my-5 border-t border-gray-100 dark:border-gray-800" />

          {/* Restore */}
          <div className="space-y-3">
            <div>
              <h3 className="text-sm font-medium text-gray-700 dark:text-gray-300">Restore from backup</h3>
              <p className="mt-0.5 text-xs text-amber-600 dark:text-amber-400">
                Warning: restoring will replace your current database and settings with the contents of the backup. This cannot be undone.
              </p>
            </div>
            <label className="flex cursor-pointer items-center gap-2.5 rounded-lg border border-dashed border-gray-300 dark:border-gray-600 px-4 py-3 text-sm transition-colors hover:border-blue-400 dark:hover:border-blue-600">
              <Upload className="h-4 w-4 shrink-0 text-gray-400" />
              <span className={backupFile ? "text-gray-800 dark:text-gray-200" : "text-gray-400 dark:text-gray-500"}>
                {backupFile ? backupFile.name : "Click to select a backup ZIP…"}
              </span>
              <input
                type="file"
                accept=".zip"
                className="hidden"
                onChange={(e) => { setBackupFile(e.target.files?.[0] ?? null); setRestoreResult(null); }}
              />
            </label>
            {backupFile && (
              <button
                onClick={handleRestoreBackup}
                disabled={restoringBackup}
                className="flex items-center gap-1.5 rounded-lg border border-amber-300 dark:border-amber-700 bg-amber-50 dark:bg-amber-950 px-4 py-2 text-sm font-medium text-amber-700 dark:text-amber-400 hover:bg-amber-100 dark:hover:bg-amber-900 disabled:opacity-60"
              >
                {restoringBackup ? <Loader2 className="h-4 w-4 animate-spin" /> : <Upload className="h-4 w-4" />}
                Restore
              </button>
            )}
            {restoreResult && (
              <div className={`flex items-center gap-2 rounded-lg border px-3 py-2 text-sm ${
                restoreResult.ok
                  ? "border-green-200 dark:border-green-900 bg-green-50 dark:bg-green-950 text-green-700 dark:text-green-400"
                  : "border-red-200 dark:border-red-900 bg-red-50 dark:bg-red-950 text-red-700 dark:text-red-400"
              }`}>
                {restoreResult.ok
                  ? <CheckCircle2 className="h-4 w-4 shrink-0" />
                  : <AlertCircle className="h-4 w-4 shrink-0" />}
                {restoreResult.ok ? restoreResult.message : restoreResult.error}
              </div>
            )}
          </div>
        </Section>

        {/* Security */}
        <Section title="Security">
          <p className="mb-4 text-xs text-gray-500 dark:text-gray-400">
            Set a password to require login when AutoRrent is accessed via Cloudflare tunnel.
            Local network access is always unrestricted. Leave blank to disable authentication.
          </p>
          <div className="space-y-3">
            <Field
              label="UI Password"
              type="password"
              value={settings.ui_password ?? ""}
              onChange={(v) => set("ui_password", v)}
              placeholder="Leave blank to disable auth"
            />
            <p className="text-xs text-gray-400 dark:text-gray-500">
              Changing the password immediately invalidates all active sessions.
            </p>
            <button
              onClick={handleRevokeAllSessions}
              className="flex items-center gap-1.5 rounded-lg border border-gray-300 dark:border-gray-600
                         px-3 py-1.5 text-sm font-medium text-gray-700 dark:text-gray-300
                         hover:bg-red-50 dark:hover:bg-red-950 hover:text-red-600 dark:hover:text-red-400
                         hover:border-red-300 dark:hover:border-red-700 transition-colors"
            >
              Revoke all sessions
            </button>
          </div>
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
        <div className="fixed bottom-20 right-4 z-50 rounded-lg bg-gray-900 dark:bg-gray-700 px-4 py-3 text-sm text-white shadow-lg md:bottom-6 md:right-6">
          {toast}
        </div>
      )}
    </div>
  );
}
