import type { Category } from "./types";

const CATEGORY_LABELS: Record<Category, string> = {
  email: "Email Marketing",
  social: "Social",
  ecommerce: "Ecommerce",
  content: "Content",
};

const PLATFORM_LABELS: Record<string, string> = { reddit: "Reddit", x: "X", instagram: "Instagram", tiktok: "TikTok" };

export const categoryLabel = (c: string): string => CATEGORY_LABELS[c as Category] ?? c;

export const platformLabel = (p: string): string => PLATFORM_LABELS[p] ?? p;

export const formatDelta = (pct: number): string => `${pct > 0 ? "+" : ""}${Math.round(pct)}%`;

/** te-aggregator's ranking version is its computed-at time: 20261006T043546442119Z. */
export function versionDate(version: string | null): Date | null {
  const m = version?.match(/^(\d{4})(\d{2})(\d{2})T(\d{2})(\d{2})(\d{2})/);
  return m ? new Date(Date.UTC(+m[1], +m[2] - 1, +m[3], +m[4], +m[5], +m[6])) : null;
}

export function timeAgo(then: Date, now: Date = new Date()): string {
  const s = Math.max(0, Math.round((now.getTime() - then.getTime()) / 1000));
  if (s < 60) return "just now";
  const m = Math.round(s / 60);
  if (m < 60) return `${m} min ago`;
  const h = Math.round(m / 60);
  if (h < 48) return `${h} h ago`;
  return `${Math.round(h / 24)} days ago`;
}

/** Posted-at times are from the replayed fixture day, so show the date and time, not "ago". */
export function formatWhen(iso: string | null): string {
  if (!iso) return "unknown";
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return iso;
  return d.toLocaleString("en-US", { month: "short", day: "numeric", hour: "numeric", minute: "2-digit", timeZone: "UTC" }) + " UTC";
}
