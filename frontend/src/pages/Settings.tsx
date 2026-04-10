import { useEffect, useState } from "react";
import {
  AlertCircle,
  CheckCircle2,
  Edit2,
  FolderOpen,
  Loader2,
  Plus,
  Save,
  Star,
  Trash2,
} from "lucide-react";
import { api, type DownloadPath, type Settings as SettingsType } from "../api/client";

// ── Path manager ──────────────────────────────────────────────────────────────

function PathModal({
  initial,
  onClose,
  onSave,
}: {
  initial?: DownloadPath;
  onClose: () => void;
  onSave: (data: { name: string; path: string; is_default: boolean }) => void;
}) {
  const [form, setForm] = useState({
    name: initial?.name ?? "",
    path: initial?.path ?? "",
    is_default: initial?.is_default ?? false,
  });

  const valid = form.name.trim() && form.path.trim();

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/40 p-4">
      <div className="w-full max-w-sm rounded-xl bg-white p-6 shadow-xl">
        <h2 className="mb-4 text-lg font-semibold text-gray-900">
          {initial ? "Edit folder" : "Add download folder"}
        </h2>
        <div className="space-y-3">
          <div>
            <label className="mb-1 block text-sm font-medium text-gray-700">
              Display name
            </label>
            <input
              value={form.name}
              onChange={(e) => setForm((f) => ({ ...f, name: e.target.value }))}
              placeholder="e.g. TV Shows"
              className="w-full rounded-lg border border-gray-300 px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-blue-500"
            />
          </div>
          <div>
            <label className="mb-1 block text-sm font-medium text-gray-700">
              Linux path
            </label>
            <input
              value={form.path}
              onChange={(e) => setForm((f) => ({ ...f, path: e.target.value }))}
              placeholder="/mnt/nas/tv"
              className="w-full rounded-lg border border-gray-300 px-3 py-2 font-mono text-sm focus:outline-none focus:ring-2 focus:ring-blue-500"
            />
          </div>
          <label className="flex cursor-pointer items-center gap-2">
            <input
              type="checkbox"
              checked={form.is_default}
              onChange={(e) => setForm((f) => ({ ...f, is_default: e.target.checked }))}
              className="h-4 w-4 rounded border-gray-300 text-blue-600"
            />
            <span className="text-sm text-gray-700">Set as default folder</span>
          </label>
        </div>
        <div className="mt-5 flex gap-3">
          <button
            onClick={onClose}
            className="flex-1 rounded-lg border border-gray-300 px-4 py-2 text-sm font-medium text-gray-700 hover:bg-gray-50"
          >
            Cancel
          </button>
          <button
            onClick={() => valid && onSave(form)}
            disabled={!valid}
            className="flex-1 rounded-lg bg-blue-600 px-4 py-2 text-sm font-medium text-white hover:bg-blue-700 disabled:opacity-50"
          >
            Save
          </button>
        </div>
      </div>
    </div>
  );
}

// ── Input component ───────────────────────────────────────────────────────────

function Field({
  label,
  hint,
  type = "text",
  value,
  onChange,
  placeholder,
}: {
  label: string;
  hint?: string;
  type?: string;
  value: string;
  onChange: (v: string) => void;
  placeholder?: string;
}) {
  return (
    <div>
      <label className="mb-1 block text-sm font-medium text-gray-700">{label}</label>
      {hint && <p className="mb-1 text-xs text-gray-500">{hint}</p>}
      <input
        type={type}
        value={value}
        onChange={(e) => onChange(e.target.value)}
        placeholder={placeholder}
        className="w-full rounded-lg border border-gray-300 px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-blue-500"
      />
    </div>
  );
}

// ── Main page ─────────────────────────────────────────────────────────────────

export default function Settings() {
  const [settings, setSettings] = useState<SettingsType>({});
  const [paths, setPaths] = useState<DownloadPath[]>([]);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [testing, setTesting] = useState(false);
  const [testResult, setTestResult] = useState<{ success: boolean; message: string } | null>(null);
  const [pathModal, setPathModal] = useState<DownloadPath | true | null>(null);
  const [toast, setToast] = useState<string | null>(null);

  const showToast = (msg: string) => {
    setToast(msg);
    setTimeout(() => setToast(null), 3000);
  };

  const load = async () => {
    const [s, p] = await Promise.all([api.settings.get(), api.paths.list()]);
    setSettings(s);
    setPaths(p);
    setLoading(false);
  };

  useEffect(() => { load(); }, []);

  const set = (k: string, v: string) => setSettings((s) => ({ ...s, [k]: v }));

  const handleSave = async () => {
    setSaving(true);
    try {
      await api.settings.update(settings);
      showToast("Settings saved!");
    } catch (e: unknown) {
      showToast(`Error: ${(e as Error).message}`);
    } finally {
      setSaving(false);
    }
  };

  const handleTestQbit = async () => {
    setTesting(true);
    setTestResult(null);
    try {
      const res = await api.settings.testQbit();
      setTestResult(res);
    } finally {
      setTesting(false);
    }
  };

  const handlePathSave = async (data: { name: string; path: string; is_default: boolean }) => {
    if (pathModal === true) {
      await api.paths.create(data);
    } else if (pathModal && typeof pathModal === "object") {
      await api.paths.update(pathModal.id, data);
    }
    setPathModal(null);
    load();
  };

  const handlePathDelete = async (id: number) => {
    if (!confirm("Remove this folder?")) return;
    await api.paths.delete(id);
    load();
  };

  if (loading) {
    return (
      <div className="flex h-full items-center justify-center">
        <Loader2 className="h-6 w-6 animate-spin text-blue-600" />
      </div>
    );
  }

  return (
    <div className="p-6 max-w-2xl">
      <div className="mb-6">
        <h1 className="text-2xl font-bold text-gray-900">Settings</h1>
        <p className="mt-1 text-sm text-gray-500">Configure your qBittorrent connection and preferences.</p>
      </div>

      <div className="space-y-6">
        {/* qBittorrent */}
        <section className="rounded-xl border border-gray-200 bg-white p-5 shadow-sm">
          <h2 className="mb-4 text-base font-semibold text-gray-900">qBittorrent Connection</h2>
          <div className="grid gap-3 sm:grid-cols-2">
            <Field
              label="Host"
              value={settings.qbit_host ?? ""}
              onChange={(v) => set("qbit_host", v)}
              placeholder="192.168.1.100"
            />
            <Field
              label="Port"
              value={settings.qbit_port ?? ""}
              onChange={(v) => set("qbit_port", v)}
              placeholder="8080"
            />
            <Field
              label="Username"
              value={settings.qbit_username ?? ""}
              onChange={(v) => set("qbit_username", v)}
              placeholder="admin"
            />
            <Field
              label="Password"
              type="password"
              value={settings.qbit_password ?? ""}
              onChange={(v) => set("qbit_password", v)}
              placeholder="••••••••"
            />
            <div className="sm:col-span-2">
              <Field
                label="Category"
                hint="Torrents added by AutoRrent will use this category in qBittorrent."
                value={settings.qbit_category ?? ""}
                onChange={(v) => set("qbit_category", v)}
                placeholder="autorrent"
              />
            </div>
          </div>
          <div className="mt-4 flex items-center gap-3">
            <button
              onClick={handleTestQbit}
              disabled={testing}
              className="flex items-center gap-1.5 rounded-lg border border-gray-300 px-4 py-2 text-sm font-medium text-gray-700 hover:bg-gray-100 disabled:opacity-50"
            >
              {testing ? <Loader2 className="h-4 w-4 animate-spin" /> : null}
              Test connection
            </button>
            {testResult && (
              <span
                className={`flex items-center gap-1.5 text-sm font-medium ${
                  testResult.success ? "text-green-600" : "text-red-600"
                }`}
              >
                {testResult.success ? (
                  <CheckCircle2 className="h-4 w-4" />
                ) : (
                  <AlertCircle className="h-4 w-4" />
                )}
                {testResult.message}
              </span>
            )}
          </div>
        </section>

        {/* Download folders */}
        <section className="rounded-xl border border-gray-200 bg-white p-5 shadow-sm">
          <div className="mb-4 flex items-center justify-between">
            <div>
              <h2 className="text-base font-semibold text-gray-900">Download Folders</h2>
              <p className="text-xs text-gray-500 mt-0.5">
                These are the folders users can choose when downloading.
              </p>
            </div>
            <button
              onClick={() => setPathModal(true)}
              className="flex items-center gap-1.5 rounded-lg bg-blue-600 px-3 py-1.5 text-sm font-medium text-white hover:bg-blue-700"
            >
              <Plus className="h-4 w-4" />
              Add folder
            </button>
          </div>

          {paths.length === 0 ? (
            <div className="rounded-lg border-2 border-dashed border-gray-200 py-8 text-center text-sm text-gray-400">
              <FolderOpen className="mx-auto mb-2 h-8 w-8 opacity-40" />
              No folders configured yet. Add one above.
            </div>
          ) : (
            <ul className="divide-y divide-gray-100">
              {paths.map((p) => (
                <li key={p.id} className="flex items-center gap-3 py-2.5">
                  <div className="flex-1 min-w-0">
                    <div className="flex items-center gap-1.5">
                      <span className="text-sm font-medium text-gray-900">{p.name}</span>
                      {p.is_default && (
                        <span className="inline-flex items-center gap-0.5 rounded-full bg-yellow-100 px-1.5 py-0.5 text-[10px] font-semibold text-yellow-700">
                          <Star className="h-2.5 w-2.5" /> Default
                        </span>
                      )}
                    </div>
                    <p className="text-xs text-gray-400 font-mono truncate">{p.path}</p>
                  </div>
                  <button
                    onClick={() => setPathModal(p)}
                    className="rounded-lg p-1.5 text-gray-400 hover:bg-gray-100"
                  >
                    <Edit2 className="h-3.5 w-3.5" />
                  </button>
                  <button
                    onClick={() => handlePathDelete(p.id)}
                    className="rounded-lg p-1.5 text-gray-400 hover:bg-red-50 hover:text-red-500"
                  >
                    <Trash2 className="h-3.5 w-3.5" />
                  </button>
                </li>
              ))}
            </ul>
          )}
        </section>

        {/* Watchlist / Scanner */}
        <section className="rounded-xl border border-gray-200 bg-white p-5 shadow-sm">
          <h2 className="mb-4 text-base font-semibold text-gray-900">Auto-scanner</h2>
          <div className="grid gap-3 sm:grid-cols-2">
            <div>
              <label className="mb-1 block text-sm font-medium text-gray-700">
                Scan interval (minutes)
              </label>
              <p className="mb-1 text-xs text-gray-500">
                How often AutoRrent checks for new episodes.
              </p>
              <input
                type="number"
                min={5}
                value={settings.scan_interval_minutes ?? "60"}
                onChange={(e) => set("scan_interval_minutes", e.target.value)}
                className="w-full rounded-lg border border-gray-300 px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-blue-500"
              />
            </div>
            <div>
              <label className="mb-1 block text-sm font-medium text-gray-700">
                Minimum seeds
              </label>
              <p className="mb-1 text-xs text-gray-500">
                Only download if a torrent has at least this many seeds.
              </p>
              <input
                type="number"
                min={0}
                value={settings.min_seeds ?? "3"}
                onChange={(e) => set("min_seeds", e.target.value)}
                className="w-full rounded-lg border border-gray-300 px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-blue-500"
              />
            </div>
          </div>

          <div className="mt-4 border-t border-gray-100 pt-4">
            <label className="flex cursor-pointer items-start gap-3">
              <input
                type="checkbox"
                checked={settings.remove_on_complete === "true"}
                onChange={(e) => set("remove_on_complete", e.target.checked ? "true" : "false")}
                className="mt-0.5 h-4 w-4 rounded border-gray-300 text-blue-600"
              />
              <div>
                <span className="text-sm font-medium text-gray-700">
                  Remove from qBittorrent when download completes
                </span>
                <p className="mt-0.5 text-xs text-gray-500">
                  Once a torrent finishes downloading and starts seeding, AutoRrent will
                  remove it from qBittorrent automatically. Your downloaded files are kept —
                  only the torrent entry is removed.
                </p>
              </div>
            </label>
          </div>
        </section>

        {/* Jackett */}
        <section className="rounded-xl border border-gray-200 bg-white p-5 shadow-sm">
          <h2 className="mb-1 text-base font-semibold text-gray-900">Jackett / Prowlarr</h2>
          <p className="mb-4 text-xs text-gray-500">
            Optional. Connect to Jackett or Prowlarr to search hundreds of additional indexers.
            Leave blank to use only the built-in NYAA and TPB sources.
          </p>
          <div className="grid gap-3 sm:grid-cols-2">
            <Field
              label="URL"
              value={settings.jackett_url ?? ""}
              onChange={(v) => set("jackett_url", v)}
              placeholder="http://localhost:9117"
            />
            <Field
              label="API Key"
              value={settings.jackett_api_key ?? ""}
              onChange={(v) => set("jackett_api_key", v)}
              placeholder="your-api-key"
            />
          </div>
        </section>

        {/* Apprise */}
        <section className="rounded-xl border border-gray-200 bg-white p-5 shadow-sm">
          <h2 className="mb-1 text-base font-semibold text-gray-900">Notifications</h2>
          <p className="mb-3 text-xs text-gray-500">
            Uses{" "}
            <a
              href="https://github.com/caronc/apprise"
              target="_blank"
              rel="noopener noreferrer"
              className="text-blue-600 underline"
            >
              Apprise
            </a>{" "}
            — supports Telegram, Discord, Slack, email, Pushover, and 60+ more. Leave blank to
            disable.
          </p>
          <Field
            label="Apprise URL"
            value={settings.apprise_url ?? ""}
            onChange={(v) => set("apprise_url", v)}
            placeholder="tgram://bottoken/ChatID"
          />
        </section>

        {/* Save */}
        <div className="flex justify-end">
          <button
            onClick={handleSave}
            disabled={saving}
            className="flex items-center gap-2 rounded-lg bg-blue-600 px-6 py-2.5 text-sm font-medium text-white hover:bg-blue-700 disabled:opacity-60"
          >
            {saving ? <Loader2 className="h-4 w-4 animate-spin" /> : <Save className="h-4 w-4" />}
            Save settings
          </button>
        </div>
      </div>

      {pathModal !== null && (
        <PathModal
          initial={pathModal === true ? undefined : pathModal}
          onClose={() => setPathModal(null)}
          onSave={handlePathSave}
        />
      )}

      {toast && (
        <div className="fixed bottom-6 right-6 z-50 rounded-lg bg-gray-900 px-4 py-3 text-sm text-white shadow-lg">
          {toast}
        </div>
      )}
    </div>
  );
}
