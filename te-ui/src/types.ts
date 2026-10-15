// Response shapes from te-api (te-api/README.md; the campaign response follows the design's API schema).

export const CATEGORIES = ["email", "social", "ecommerce", "content"] as const;
export type Category = (typeof CATEGORIES)[number];

export interface TrendListItem {
  trendId: string;
  name: string;
  category: Category;
  velocityScore: number;
  deltaPct: number;
  firstSeenAt: string | null;
  sources: string[];
  sparkline: number[];
  rank: number;
  velocityLabel: "spike" | "rising" | string;
  summary: string;
}

export interface SamplePost {
  text: string;
  source: string;
  postedAt: string;
  author: string | null;
}

export interface Signal {
  type: "term" | "cluster" | string;
  ref: string;
  zScore: number;
  frequency: number;
  baseline: number;
  windowStart: string;
}

export interface TrendDetail extends TrendListItem {
  clusterId: string | null;
  relatedTerms: string[];
  samplePosts: SamplePost[];
  growth: { date: string; score: number }[];
  /** Why it's trending, in plain words (te-aggregator). Absent on rankings from before it existed. */
  highlights?: string[] | null;
  /** The raw scores behind the ranking: for the API and debugging, not shown on the dashboard. */
  signals: Signal[];
}

export interface TrendPage {
  items: TrendListItem[];
  nextCursor: string | null;
  /** Ranking version (te-aggregator's computed-at timestamp), from X-Trends-Version. */
  version: string | null;
  /** "cache" | "database", from X-Trends-Source. */
  source: string | null;
}

/** The voice of the campaign copy (te-api's `tone`). */
export const TONES = ["casual", "professional", "expert"] as const;
export type Tone = (typeof TONES)[number];

/** What goes into the campaign: POST /v1/campaigns/draft-block/preview returns it
 *  before inserting, POST /v1/campaigns/draft-block after. */
export interface CampaignCopy {
  trendId: string;
  tone: Tone;
  subjectLine: string;
  previewText: string;
  headline: string;
  suggestedCopy: string; // the content block itself (the design doc's field)
}

export interface DraftBlock extends CampaignCopy {
  blockId: string;
  status: string;
  campaignId: string;
  /** Mailchimp's web view of the draft (the fake's /archive page in the PoC). */
  previewUrl: string | null;
}
