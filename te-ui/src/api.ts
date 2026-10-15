// Same-origin calls: nginx (or webpack-dev-server) proxies /v1 to te-api, so the
// bundle has no API URLs baked in. te-api calls Mailchimp for the campaign step.
import type { CampaignCopy, Category, DraftBlock, Tone, TrendDetail, TrendPage } from "./types";

export class ApiError extends Error {
  constructor(public status: number, public code: string, message: string) {
    super(message);
    this.name = "ApiError";
  }
}

async function errorFrom(res: Response): Promise<ApiError> {
  try {
    const body = await res.json();
    return new ApiError(res.status, body.error ?? "error", body.message ?? res.statusText);
  } catch {
    // Not te-api's JSON: the proxy answering because te-api itself is down.
    if ([502, 503, 504].includes(res.status)) {
      return new ApiError(res.status, "unavailable", "The trends service can't be reached right now. Try again shortly.");
    }
    return new ApiError(res.status, "error", res.statusText || `HTTP ${res.status}`);
  }
}

async function request(input: string, init?: RequestInit): Promise<Response> {
  try {
    return await fetch(input, init);
  } catch (e) {
    if ((e as Error).name === "AbortError") throw e;
    throw new ApiError(0, "network", "Can't reach the server. Is the stack running?");
  }
}

export interface ListParams {
  category?: Category | null;
  cursor?: string | null;
  limit?: number;
  signal?: AbortSignal;
}

export async function fetchTrends({ category, cursor, limit = 20, signal }: ListParams = {}): Promise<TrendPage> {
  const q = new URLSearchParams({ limit: String(limit) });
  if (category) q.set("category", category);
  if (cursor) q.set("cursor", cursor);
  const res = await request(`/v1/trends?${q}`, { signal });
  if (!res.ok) throw await errorFrom(res);
  const body = await res.json();
  return {
    items: body.items ?? [],
    nextCursor: body.nextCursor ?? null,
    version: res.headers.get("X-Trends-Version") || null,
    source: res.headers.get("X-Trends-Source") || null,
  };
}

export async function fetchTrend(trendId: string, signal?: AbortSignal): Promise<TrendDetail> {
  const res = await request(`/v1/trends/${encodeURIComponent(trendId)}`, { signal });
  if (!res.ok) throw await errorFrom(res);
  return res.json();
}

async function postJson(path: string, body: unknown, signal?: AbortSignal): Promise<Response> {
  return request(path, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body), signal });
}

/** The copy that inserting would put into the campaign. No side effects. */
export async function previewCampaignBlock(trendId: string, tone: Tone, signal?: AbortSignal): Promise<CampaignCopy> {
  const res = await postJson("/v1/campaigns/draft-block/preview", { trendId, campaignId: null, tone }, signal);
  if (!res.ok) throw await errorFrom(res);
  return res.json();
}

/** te-api creates a draft Mailchimp campaign holding the trend's copy.
 *  Its errors carry a code: mailchimp_unavailable / mailchimp_error (502),
 *  trend_not_found / campaign_not_found (404). */
export async function insertIntoCampaign(trendId: string, tone: Tone = "casual"): Promise<DraftBlock> {
  const res = await postJson("/v1/campaigns/draft-block", { trendId, campaignId: null, tone });
  if (!res.ok) throw await errorFrom(res);
  return res.json();
}
