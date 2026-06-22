export function fmtSize(b?: number | null): string {
  if (!b) return "—";
  for (const [u, d] of [["TB", 1e12], ["GB", 1e9], ["MB", 1e6], ["KB", 1e3]] as [string, number][]) {
    if (b >= d) return `${(b / d).toFixed(1)} ${u}`;
  }
  return `${b} B`;
}

export function fmtDate(s?: string | null): string {
  if (!s) return "—";
  return new Date(s).toLocaleString(undefined, {
    month: "short",
    day: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  });
}

export function fmtAgo(s?: string | null): string {
  if (!s) return "—";
  const diff = Math.floor((Date.now() - new Date(s).getTime()) / 1000);
  if (diff < 60) return "just now";
  if (diff < 3600) return `${Math.floor(diff / 60)}m ago`;
  if (diff < 86400) return `${Math.floor(diff / 3600)}h ago`;
  return `${Math.floor(diff / 86400)}d ago`;
}
