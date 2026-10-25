import { useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";
import { AlertCircle, CheckCircle2, Download, FolderOpen, Loader2 } from "lucide-react";
import { api, type DownloadPath, type JackettTestResult } from "../api/client";

const FIELD = [
  "w-full rounded-lg border border-gray-300 dark:border-gray-600 px-3 py-2 text-sm",
  "focus:outline-none focus:ring-2 focus:ring-blue-500",
  "dark:bg-gray-800 dark:text-gray-100",
].join(" ");
const LABEL = "mb-1 block text-sm font-medium text-gray-700 dark:text-gray-300";

const STEPS = ["Welcome", "qBittorrent", "Folder", "Sources", "Done"] as const;

function Result({ ok, children }: { ok: boolean; children: React.ReactNode }) {
  return (
    <p className={`mt-2 flex items-start gap-1.5 text-sm ${ok ? "text-green-600 dark:text-green-400" : "text-amber-700 dark:text-amber-400"}`}>
      {ok ? <CheckCircle2 className="mt-0.5 h-4 w-4 shrink-0" /> : <AlertCircle className="mt-0.5 h-4 w-4 shrink-0" />}
      <span>{children}</span>
    </p>
  );
}

/** First-run setup: qBittorrent, a download folder and (optionally) Prowlarr. Fully skippable. */
export default function OnboardingWizard({ onClose }: { onClose: () => void }) {
  const navigate = useNavigate();
  const [step, setStep] = useState(0);
  const [busy, setBusy] = useState(false);

  // qBittorrent
  const [host, setHost] = useState("localhost");
  const [port, setPort] = useState("8080");
  const [user, setUser] = useState("admin");
  const [pass, setPass] = useState("");
  const [qbit, setQbit] = useState<{ ok: boolean; text: string } | null>(null);

  // folder
  const [paths, setPaths] = useState<DownloadPath[]>([]);
  const [folderName, setFolderName] = useState("TV Shows");
  const [folderPath, setFolderPath] = useState("");
  const [folderError, setFolderError] = useState<string | null>(null);

  // sources
  const [pUrl, setPUrl] = useState("");
  const [pKey, setPKey] = useState("");
  const [probe, setProbe] = useState<JackettTestResult | null>(null);

  useEffect(() => {
    api.settings.get().then((s) => {
      setHost(s.qbit_host || "localhost");
      setPort(s.qbit_port || "8080");
      setUser(s.qbit_username || "admin");
    }).catch(() => {});
  }, []);

  useEffect(() => {
    if (STEPS[step] === "Folder") api.paths.list().then(setPaths).catch(() => {});
  }, [step]);

  const saveQbit = () => api.settings.update({
    qbit_host: host.trim(), qbit_port: port.trim(), qbit_username: user.trim(), qbit_password: pass,
  });

  const testQbit = async () => {
    setBusy(true);
    setQbit(null);
    try {
      await saveQbit();   // the connection test uses the saved settings
      const r = await api.settings.testQbit();
      setQbit({ ok: r.success, text: r.message });
    } catch (err) {
      setQbit({ ok: false, text: err instanceof Error ? err.message : "Test failed." });
    } finally { setBusy(false); }
  };

  const addFolder = async () => {
    setBusy(true);
    setFolderError(null);
    try {
      await api.paths.create({ name: folderName.trim(), path: folderPath.trim(), is_default: paths.length === 0 });
      setPaths(await api.paths.list());
      setFolderPath("");
    } catch (err) {
      setFolderError(err instanceof Error ? err.message : "Could not add the folder.");
    } finally { setBusy(false); }
  };

  const testSources = async () => {
    setBusy(true);
    setProbe(null);
    try { setProbe(await api.settings.testJackett({ url: pUrl.trim(), api_key: pKey.trim() })); }
    catch (err) { setProbe({ ok: false, error: err instanceof Error ? err.message : "Test failed." }); }
    finally { setBusy(false); }
  };

  const finish = async (goTo?: string) => {
    setBusy(true);
    try { await api.onboarding.complete(); } catch { /* shown again next time at worst */ }
    onClose();
    if (goTo) navigate(goTo);
  };

  const skip = () => {
    if (confirm("Skip setup? You can configure everything later in Settings.")) finish();
  };

  const next = async () => {
    if (STEPS[step] === "Sources" && pUrl.trim() && pKey.trim()) {
      setBusy(true);
      try { await api.settings.update({ jackett_url: pUrl.trim(), jackett_api_key: pKey.trim() }); }
      finally { setBusy(false); }
    }
    setStep((s) => s + 1);
  };

  const name = STEPS[step];
  const primary = "rounded-lg bg-blue-600 px-4 py-2 text-sm font-medium text-white hover:bg-blue-700 disabled:opacity-50";
  const secondary = "rounded-lg border border-gray-300 dark:border-gray-600 px-4 py-2 text-sm font-medium text-gray-700 dark:text-gray-300 hover:bg-gray-50 dark:hover:bg-gray-800 disabled:opacity-50";

  return (
    <div className="fixed inset-0 z-[60] flex items-center justify-center bg-black/60 p-4" role="dialog" aria-modal="true" aria-labelledby="onb-title">
      <div className="max-h-[92vh] w-full max-w-lg overflow-y-auto rounded-xl border border-gray-200 dark:border-gray-700 bg-white dark:bg-gray-900 p-6 shadow-2xl">
        <div className="mb-5 flex items-center gap-1.5" aria-label={`Step ${step + 1} of ${STEPS.length}`}>
          {STEPS.map((s, i) => (
            <span key={s} className={`h-1.5 flex-1 rounded-full ${i <= step ? "bg-blue-600" : "bg-gray-200 dark:bg-gray-700"}`} />
          ))}
        </div>

        {name === "Welcome" && (
          <>
            <div className="mb-3 flex h-11 w-11 items-center justify-center rounded-xl bg-blue-600"><Download className="h-5 w-5 text-white" /></div>
            <h2 id="onb-title" className="text-xl font-semibold text-gray-900 dark:text-gray-100">Welcome to AutoRrent</h2>
            <p className="mt-2 text-sm text-gray-600 dark:text-gray-400">
              AutoRrent watches the shows you pick and sends new episodes to qBittorrent automatically. Three quick steps get you going:
            </p>
            <ol className="mt-3 list-decimal space-y-1 pl-5 text-sm text-gray-700 dark:text-gray-300">
              <li>Connect to qBittorrent</li>
              <li>Choose where downloads are saved</li>
              <li>Optionally add more sources (Prowlarr / Jackett)</li>
            </ol>
          </>
        )}

        {name === "qBittorrent" && (
          <>
            <h2 id="onb-title" className="text-lg font-semibold text-gray-900 dark:text-gray-100">Connect to qBittorrent</h2>
            <p className="mt-1 text-xs text-gray-500 dark:text-gray-400">
              Turn on the Web UI in qBittorrent (Options → Web UI) and enter its address below. If AutoRrent runs in
              Docker, <span className="font-mono">localhost</span> means the AutoRrent container itself — use your
              server's IP address or the qBittorrent container's name instead.
            </p>
            <div className="mt-3 grid gap-3 sm:grid-cols-3">
              <div className="sm:col-span-2"><label className={LABEL}>Host</label>
                <input value={host} onChange={(e) => setHost(e.target.value)} className={FIELD} autoComplete="off" /></div>
              <div><label className={LABEL}>Port</label>
                <input value={port} onChange={(e) => setPort(e.target.value)} className={FIELD} inputMode="numeric" autoComplete="off" /></div>
              <div><label className={LABEL}>Username</label>
                <input value={user} onChange={(e) => setUser(e.target.value)} className={FIELD} autoComplete="off" /></div>
              <div className="sm:col-span-2"><label className={LABEL}>Password</label>
                <input type="password" value={pass} onChange={(e) => setPass(e.target.value)} className={FIELD} autoComplete="new-password" /></div>
            </div>
            <button onClick={testQbit} disabled={busy || !host.trim()} className={`${secondary} mt-3 inline-flex items-center gap-1.5`}>
              {busy && <Loader2 className="h-3.5 w-3.5 animate-spin" />}Test connection
            </button>
            {qbit && <Result ok={qbit.ok}>{qbit.ok ? qbit.text : `${qbit.text} — you can continue and fix this later in Settings.`}</Result>}
          </>
        )}

        {name === "Folder" && (
          <>
            <h2 id="onb-title" className="text-lg font-semibold text-gray-900 dark:text-gray-100">Where should downloads go?</h2>
            <p className="mt-1 text-xs text-gray-500 dark:text-gray-400">
              Enter the folder as <em>qBittorrent</em> sees it (for example your Plex TV library). You can add more later.
            </p>
            {paths.length > 0 && (
              <ul className="mt-3 divide-y divide-gray-100 dark:divide-gray-800 rounded-lg border border-gray-200 dark:border-gray-700">
                {paths.map((p) => (
                  <li key={p.id} className="flex items-center gap-2 px-3 py-2 text-sm">
                    <FolderOpen className="h-4 w-4 shrink-0 text-gray-400" />
                    <span className="font-medium text-gray-900 dark:text-gray-100">{p.name}</span>
                    <span className="min-w-0 flex-1 truncate font-mono text-xs text-gray-500 dark:text-gray-400">{p.path}</span>
                    {p.free_bytes != null && <span className="shrink-0 text-xs text-gray-400">{(p.free_bytes / 1e9).toFixed(0)} GB free</span>}
                  </li>
                ))}
              </ul>
            )}
            <div className="mt-3 grid gap-3 sm:grid-cols-3">
              <div><label className={LABEL}>Name</label>
                <input value={folderName} onChange={(e) => setFolderName(e.target.value)} className={FIELD} /></div>
              <div className="sm:col-span-2"><label className={LABEL}>Path</label>
                <input value={folderPath} onChange={(e) => setFolderPath(e.target.value)} placeholder="/mnt/media/tv" className={`${FIELD} font-mono`} /></div>
            </div>
            <button onClick={addFolder} disabled={busy || !folderName.trim() || !folderPath.trim()} className={`${secondary} mt-3 inline-flex items-center gap-1.5`}>
              {busy && <Loader2 className="h-3.5 w-3.5 animate-spin" />}{paths.length ? "Add another folder" : "Add folder"}
            </button>
            {folderError && <Result ok={false}>{folderError}</Result>}
            {paths.length === 0 && !folderError && (
              <p className="mt-2 text-xs text-gray-400 dark:text-gray-500">Skipping this saves downloads to /downloads.</p>
            )}
          </>
        )}

        {name === "Sources" && (
          <>
            <h2 id="onb-title" className="text-lg font-semibold text-gray-900 dark:text-gray-100">Find more sources</h2>
            <p className="mt-1 text-sm text-gray-600 dark:text-gray-400">
              Built-in sources (NYAA, The Pirate Bay, EZTV, YTS, TorrentGalaxy, 1337x) already work. For hundreds more —
              including private trackers — connect Prowlarr or Jackett. This is optional.
            </p>
            <div className="mt-3 grid gap-3 sm:grid-cols-2">
              <div><label className={LABEL}>Prowlarr / Jackett URL</label>
                <input value={pUrl} onChange={(e) => setPUrl(e.target.value)} placeholder="http://192.168.1.10:9696" className={FIELD} autoComplete="off" /></div>
              <div><label className={LABEL}>API key</label>
                <input value={pKey} onChange={(e) => setPKey(e.target.value)} className={FIELD} autoComplete="off" /></div>
            </div>
            <button onClick={testSources} disabled={busy || !pUrl.trim() || !pKey.trim()} className={`${secondary} mt-3 inline-flex items-center gap-1.5`}>
              {busy && <Loader2 className="h-3.5 w-3.5 animate-spin" />}Test connection
            </button>
            {probe && (
              <Result ok={probe.ok}>
                {probe.ok
                  ? `Connected to ${probe.kind}${probe.version ? ` ${probe.version}` : ""} — ${probe.indexers} indexer${probe.indexers === 1 ? "" : "s"} enabled`
                  : probe.error}
              </Result>
            )}
          </>
        )}

        {name === "Done" && (
          <>
            <h2 id="onb-title" className="text-xl font-semibold text-gray-900 dark:text-gray-100">You're set</h2>
            <p className="mt-2 text-sm text-gray-600 dark:text-gray-400">
              Next, tell AutoRrent what to download. Browse what's airing this week and add a show with one click, or
              add shows from the Watchlist. Everything here can be changed in Settings.
            </p>
            <ul className="mt-3 space-y-1 text-sm text-gray-700 dark:text-gray-300">
              <li className="flex items-center gap-1.5"><CheckCircle2 className={`h-4 w-4 ${qbit?.ok ? "text-green-500" : "text-gray-300"}`} /> qBittorrent {qbit?.ok ? "connected" : "not tested"}</li>
              <li className="flex items-center gap-1.5"><CheckCircle2 className={`h-4 w-4 ${paths.length ? "text-green-500" : "text-gray-300"}`} /> {paths.length ? `${paths.length} download folder${paths.length === 1 ? "" : "s"}` : "Default download folder"}</li>
              <li className="flex items-center gap-1.5"><CheckCircle2 className={`h-4 w-4 ${pUrl.trim() && pKey.trim() ? "text-green-500" : "text-gray-300"}`} /> {pUrl.trim() && pKey.trim() ? "Prowlarr / Jackett connected" : "Built-in sources only"}</li>
            </ul>
          </>
        )}

        <div className="mt-6 flex items-center gap-3">
          {name !== "Done" && (
            <button onClick={skip} disabled={busy} className="text-xs text-gray-400 dark:text-gray-500 hover:underline disabled:opacity-50">Skip setup</button>
          )}
          <div className="ml-auto flex gap-2">
            {step > 0 && name !== "Done" && <button onClick={() => setStep((s) => s - 1)} disabled={busy} className={secondary}>Back</button>}
            {name === "Welcome" && <button onClick={next} className={primary}>Get started</button>}
            {(name === "qBittorrent" || name === "Folder" || name === "Sources") && (
              <button onClick={next} disabled={busy} className={primary}>
                {name === "Sources" && !(pUrl.trim() && pKey.trim()) ? "Skip" : name === "Folder" && paths.length === 0 ? "Skip" : "Next"}
              </button>
            )}
            {name === "Done" && (
              <>
                <button onClick={() => finish()} disabled={busy} className={secondary}>Finish</button>
                <button onClick={() => finish("/browse")} disabled={busy} className={primary}>Browse shows</button>
              </>
            )}
          </div>
        </div>
      </div>
    </div>
  );
}
