import { useCallback, useEffect, useRef, useState } from "react";
import { AlertCircle, CheckCircle2, Clock, Download, RefreshCw, Undo2, Terminal, Wifi, WifiOff } from "lucide-react";
import { api, type UpdateStatus } from "../api/client";
import { marked } from "marked";

marked.setOptions({ async: false });

function renderMarkdown(md: string): string {
  const result = marked.parse(md) as string;
  return result;
}

type UpgradeState = "requested" | "running" | "succeeded" | "failed" | null;

function StateChip({ state }: { state: UpgradeState }) {
  if (!state) return null;
  const map: Record<NonNullable<UpgradeState>, { label: string; className: string }> = {
    requested: { label: "Queued",    className: "bg-yellow-100 dark:bg-yellow-900/30 text-yellow-800 dark:text-yellow-300" },
    running:   { label: "Upgrading", className: "bg-blue-100 dark:bg-blue-900/30 text-blue-800 dark:text-blue-300 animate-pulse" },
    succeeded: { label: "Succeeded", className: "bg-green-100 dark:bg-green-900/30 text-green-800 dark:text-green-300" },
    failed:    { label: "Failed",    className: "bg-red-100 dark:bg-red-900/30 text-red-800 dark:text-red-300" },
  };
  const { label, className } = map[state];
  return (
    <span className={`inline-flex items-center rounded-full px-2.5 py-0.5 text-xs font-medium ${className}`}>
      {label}
    </span>
  );
}

export default function Updates() {
  const [status, setStatus] = useState<UpdateStatus | null>(null);
  const [loading, setLoading] = useState(true);
  const [checking, setChecking] = useState(false);
  const [upgrading, setUpgrading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [confirm, setConfirm] = useState<"upgrade" | "rollback" | null>(null);
  const [showLog, setShowLog] = useState(false);
  const logRef = useRef<HTMLPreElement>(null);
  const pollRef = useRef<ReturnType<typeof setInterval> | null>(null);

  const load = useCallback(async (quiet = false) => {
    if (!quiet) setLoading(true);
    try {
      const s = await api.updates.status();
      setStatus(s);
      setError(null);
    } catch (e) {
      setError((e as Error).message);
    } finally {
      if (!quiet) setLoading(false);
    }
  }, []);

  // Poll while upgrade is in-progress
  useEffect(() => {
    const inProgress = status?.upgrade_state === "requested" || status?.upgrade_state === "running";
    if (inProgress && !pollRef.current) {
      pollRef.current = setInterval(() => load(true), 5000);
    }
    if (!inProgress && pollRef.current) {
      clearInterval(pollRef.current);
      pollRef.current = null;
    }
    return () => {
      if (pollRef.current) { clearInterval(pollRef.current); pollRef.current = null; }
    };
  }, [status?.upgrade_state, load]);

  useEffect(() => { load(); }, [load]);

  // Auto-scroll log
  useEffect(() => {
    if (logRef.current) {
      logRef.current.scrollTop = logRef.current.scrollHeight;
    }
  }, [status?.upgrade_log]);

  const handleCheck = async () => {
    setChecking(true);
    try {
      const s = await api.updates.check();
      setStatus(s);
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setChecking(false);
    }
  };

  const handleUpgrade = async () => {
    const rollback = confirm === "rollback";
    const version = rollback ? status?.previous_version : status?.latest_release?.version;
    if (!version) return;
    setConfirm(null);
    setUpgrading(true);
    setShowLog(true);
    try {
      const s = await api.updates.upgrade(version, rollback);
      setStatus(s);
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setUpgrading(false);
    }
  };

  if (loading) {
    return (
      <div className="flex items-center justify-center h-64">
        <RefreshCw className="h-6 w-6 animate-spin text-blue-500" />
      </div>
    );
  }

  const inProgress = status?.upgrade_state === "requested" || status?.upgrade_state === "running";

  return (
    <div className="max-w-3xl mx-auto px-4 py-8 space-y-6">
      <div className="flex items-center justify-between">
        <h1 className="text-2xl font-bold text-gray-900 dark:text-gray-100">Updates</h1>
        <button
          onClick={handleCheck}
          disabled={checking || inProgress}
          className="flex items-center gap-2 rounded-lg bg-white dark:bg-gray-800 border border-gray-200 dark:border-gray-700 px-3 py-2 text-sm font-medium text-gray-700 dark:text-gray-300 hover:bg-gray-50 dark:hover:bg-gray-700 disabled:opacity-50 transition-colors"
        >
          <RefreshCw className={`h-4 w-4 ${checking ? "animate-spin" : ""}`} />
          {checking ? "Checking…" : "Check now"}
        </button>
      </div>

      {error && (
        <div className="flex items-start gap-3 rounded-lg border border-red-200 dark:border-red-800 bg-red-50 dark:bg-red-950/30 p-4 text-sm text-red-700 dark:text-red-400">
          <AlertCircle className="h-4 w-4 shrink-0 mt-0.5" />
          {error}
        </div>
      )}

      {/* Version info */}
      <div className="rounded-xl border border-gray-200 dark:border-gray-700 bg-white dark:bg-gray-900 shadow-sm divide-y divide-gray-100 dark:divide-gray-800">
        <div className="flex items-center justify-between px-5 py-4">
          <div>
            <p className="text-xs text-gray-500 dark:text-gray-400">Running version</p>
            <p className="text-xl font-semibold text-gray-900 dark:text-gray-100 font-mono">
              v{status?.current_version}
            </p>
          </div>
          <div className="text-right">
            <p className="text-xs text-gray-500 dark:text-gray-400">Latest release</p>
            {status?.latest_release ? (
              <p className="text-xl font-semibold text-gray-900 dark:text-gray-100 font-mono">
                v{status.latest_release.version}
              </p>
            ) : (
              <p className="text-sm text-gray-400 dark:text-gray-500">—</p>
            )}
          </div>
        </div>

        <div className="px-5 py-4 flex items-center justify-between">
          <div className="flex items-center gap-2">
            {status?.update_available ? (
              <>
                <Download className="h-4 w-4 text-blue-500" />
                <span className="text-sm font-medium text-blue-700 dark:text-blue-400">
                  Update available
                </span>
              </>
            ) : (
              <>
                <CheckCircle2 className="h-4 w-4 text-green-500" />
                <span className="text-sm text-gray-600 dark:text-gray-400">Up to date</span>
              </>
            )}
          </div>
          <div className="flex items-center gap-3">
            {status?.upgrade_state && <StateChip state={status.upgrade_state} />}
            {status?.can_upgrade && (
              <button
                onClick={() => setConfirm("upgrade")}
                disabled={upgrading || inProgress}
                className="flex items-center gap-1.5 rounded-lg bg-blue-600 hover:bg-blue-700 disabled:bg-blue-400 text-white px-3 py-2 text-sm font-medium transition-colors"
              >
                <Download className="h-4 w-4" />
                Upgrade to v{status.latest_release?.version}
              </button>
            )}
            {status?.can_rollback && (
              <button
                onClick={() => setConfirm("rollback")}
                disabled={upgrading || inProgress}
                className="flex items-center gap-1.5 rounded-lg border border-gray-300 dark:border-gray-600 px-3 py-2 text-sm font-medium text-gray-700 dark:text-gray-300 hover:bg-gray-50 dark:hover:bg-gray-800 disabled:opacity-50 transition-colors"
              >
                <Undo2 className="h-4 w-4" />
                Roll back to v{status.previous_version}
              </button>
            )}
          </div>
        </div>

        {status?.checked_at && (
          <div className="flex items-center gap-1.5 px-5 py-2.5 text-xs text-gray-400 dark:text-gray-500">
            <Clock className="h-3.5 w-3.5" />
            Last checked {new Date(status.checked_at).toLocaleString()}
            {status.check_error && (
              <span className="ml-2 text-orange-500 dark:text-orange-400">
                (check failed: {status.check_error})
              </span>
            )}
          </div>
        )}
      </div>

      {/* Helper status */}
      <div className="rounded-xl border border-gray-200 dark:border-gray-700 bg-white dark:bg-gray-900 shadow-sm px-5 py-4">
        <h2 className="text-sm font-semibold text-gray-700 dark:text-gray-300 mb-3">Upgrade Helper</h2>
        <div className="flex items-start gap-3">
          {status?.helper.connected ? (
            <Wifi className="h-5 w-5 text-green-500 mt-0.5 shrink-0" />
          ) : (
            <WifiOff className="h-5 w-5 text-gray-400 mt-0.5 shrink-0" />
          )}
          <div className="flex-1 min-w-0">
            {status?.helper.connected ? (
              <p className="text-sm text-green-700 dark:text-green-400 font-medium">
                Connected (heartbeat every {status.helper.every_minutes} min)
              </p>
            ) : status?.helper.configured ? (
              <p className="text-sm text-orange-600 dark:text-orange-400 font-medium">
                Helper installed but not reporting — check cron
              </p>
            ) : (
              <>
                <p className="text-sm text-gray-700 dark:text-gray-300 font-medium">Not installed</p>
                <p className="text-xs text-gray-500 dark:text-gray-400 mt-1">
                  In-app upgrades require the host helper to be running. Install it with:
                </p>
                <code className="mt-2 block rounded bg-gray-100 dark:bg-gray-800 px-3 py-2 text-xs font-mono text-gray-800 dark:text-gray-200 break-all">
                  {status?.helper_install_cmd ?? "sudo bash scripts/upgrade-agent.sh --install"}
                </code>
              </>
            )}
            {status?.helper.last_seen && (
              <p className="text-xs text-gray-400 dark:text-gray-500 mt-1">
                Last seen: {new Date(status.helper.last_seen).toLocaleString()}
              </p>
            )}
          </div>
        </div>
      </div>

      {/* Release notes */}
      {status?.latest_release && (
        <div className="rounded-xl border border-gray-200 dark:border-gray-700 bg-white dark:bg-gray-900 shadow-sm px-5 py-4">
          <div className="flex items-center justify-between mb-3">
            <h2 className="text-sm font-semibold text-gray-700 dark:text-gray-300">
              Release notes — {status.latest_release.name}
            </h2>
            {status.latest_release.url && (
              <a
                href={status.latest_release.url}
                target="_blank"
                rel="noreferrer"
                className="text-xs text-blue-600 dark:text-blue-400 hover:underline"
              >
                View on GitHub ↗
              </a>
            )}
          </div>
          {status.latest_release.notes ? (
            <div
              className="prose prose-sm dark:prose-invert max-w-none text-gray-700 dark:text-gray-300"
              dangerouslySetInnerHTML={{ __html: renderMarkdown(status.latest_release.notes) }}
            />
          ) : (
            <p className="text-sm text-gray-400 dark:text-gray-500">No release notes.</p>
          )}
        </div>
      )}

      {/* Upgrade log */}
      {(showLog || status?.upgrade_log) && (
        <div className="rounded-xl border border-gray-200 dark:border-gray-700 bg-white dark:bg-gray-900 shadow-sm">
          <button
            onClick={() => setShowLog((v) => !v)}
            className="flex w-full items-center gap-2 px-5 py-3 text-sm font-medium text-gray-700 dark:text-gray-300 hover:bg-gray-50 dark:hover:bg-gray-800 rounded-xl transition-colors"
          >
            <Terminal className="h-4 w-4" />
            Upgrade log
            <span className="ml-auto text-gray-400">{showLog ? "▲" : "▼"}</span>
          </button>
          {showLog && (
            <pre
              ref={logRef}
              className="max-h-96 overflow-y-auto rounded-b-xl bg-gray-950 px-5 py-4 text-xs font-mono text-green-400 whitespace-pre-wrap"
            >
              {status?.upgrade_log || "(no log yet)"}
            </pre>
          )}
        </div>
      )}

      {/* Confirm dialog */}
      {confirm && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/50">
          <div className="w-full max-w-md rounded-2xl bg-white dark:bg-gray-900 shadow-2xl p-6 mx-4">
            <h3 className="text-lg font-semibold text-gray-900 dark:text-gray-100 mb-2">
              {confirm === "rollback" ? "Confirm rollback" : "Confirm upgrade"}
            </h3>
            <p className="text-sm text-gray-600 dark:text-gray-400 mb-6">
              {confirm === "rollback" ? "Roll back" : "Upgrade"} from <strong>v{status?.current_version}</strong> to{" "}
              <strong>v{confirm === "rollback" ? status?.previous_version : status?.latest_release?.version}</strong>?
              The container will restart — this will briefly interrupt service.
              {confirm === "rollback" && " Your data stays as it is; if you have used features added since, back up first (Settings → Data management)."}
            </p>
            <div className="flex gap-3 justify-end">
              <button
                onClick={() => setConfirm(null)}
                className="rounded-lg border border-gray-200 dark:border-gray-700 px-4 py-2 text-sm font-medium text-gray-700 dark:text-gray-300 hover:bg-gray-50 dark:hover:bg-gray-800 transition-colors"
              >
                Cancel
              </button>
              <button
                onClick={handleUpgrade}
                className="rounded-lg bg-blue-600 hover:bg-blue-700 text-white px-4 py-2 text-sm font-medium transition-colors"
              >
                {confirm === "rollback" ? "Roll back" : "Upgrade"}
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
