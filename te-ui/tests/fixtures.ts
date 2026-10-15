// Fixture data in te-api's response shapes (te-api/README.md), modelled on the replayed fixture day.
import type { TrendDetail, TrendListItem } from "../src/types";

export const LOWERCASE: TrendListItem = {
  trendId: "3f0c1a52-8d4e-5b7a-9c21-0e6f2d4b8a10",
  name: "#lowercase / #brandvoice",
  category: "social",
  velocityScore: 6.21,
  deltaPct: 984,
  firstSeenAt: "2026-09-07T17:02:11Z",
  sources: ["x", "tiktok", "instagram", "reddit"],
  sparkline: [0, 0, 1, 2, 5, 9, 31, 24, 8, 3, 1, 0],
  rank: 1,
  velocityLabel: "spike",
  summary: "84 posts in the last 12 hours on X, TikTok, Instagram and Reddit, about 11 times its usual pace.",
};

export const DUPES: TrendListItem = {
  trendId: "7a9b2c3d-1e2f-5a6b-8c7d-9e0f1a2b3c4d",
  name: "#dupe / #dupealert",
  category: "ecommerce",
  velocityScore: 3.4,
  deltaPct: 265,
  firstSeenAt: "2026-09-07T02:15:00Z",
  sources: ["reddit", "x"],
  sparkline: [5, 6, 8, 9, 12, 16, 21, 27, 33, 30, 28, 25],
  rank: 2,
  velocityLabel: "rising",
  summary: "120 posts in the last 12 hours on Reddit and X, about 4 times its usual pace.",
};

export const QUIET_LUXURY: TrendListItem = {
  trendId: "c1d2e3f4-a5b6-5c7d-8e9f-0a1b2c3d4e5f",
  name: "#quietluxury",
  category: "content",
  velocityScore: 1.1,
  deltaPct: -38,
  firstSeenAt: "2026-09-07T00:40:00Z",
  sources: ["instagram"],
  sparkline: [30, 28, 25, 22, 18, 15, 12, 10, 9, 8, 7, 6],
  rank: 3,
  velocityLabel: "rising",
  summary: "40 posts in the last 12 hours on Instagram, below its usual pace.",
};

export function detailOf(t: TrendListItem): TrendDetail {
  return {
    ...t,
    clusterId: "0b1c2d3e-4f5a-4b6c-8d7e-9f0a1b2c3d4e",
    relatedTerms: ["#brandvoice", "#copywriting", "lowercase captions"],
    samplePosts: [
      { text: `${t.name.split(" ")[0]} everything now, even the receipts`, source: "x", postedAt: "2026-09-07T21:14:00Z", author: "@maya.writes" },
      { text: "our whole brand went lowercase and engagement doubled", source: "tiktok", postedAt: "2026-09-07T21:40:00Z", author: null },
    ],
    growth: t.sparkline.map((score, i) => ({ date: `2026-09-07T${String(12 + i).padStart(2, "0")}:00:00Z`, score })),
    highlights: [
      `${t.name.split(" ")[0]} jumped to 31 posts in the 9 PM UTC hour (usually about 3 an hour).`,
      "Started on X, then spread to TikTok and Instagram within 4 hours.",
    ],
    signals: [{ type: "term", ref: t.name.split(" ")[0], zScore: 7.42, frequency: 31, baseline: 2.6, windowStart: "2026-09-07T21:00:00Z" }],
  };
}

export const VERSION = "20261006T043546442119Z";
