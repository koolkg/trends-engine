-- te-aggregator (Phase 3). Two additions to trend_ranking; idempotent like 002.
--
-- detail: everything te-api serves for a trend (name, summary, sparkline,
--   sources, sample posts, related terms, growth). The design doc's
--   trend_ranking only has label/category/score/rank, but te-api's Postgres
--   fallback (on a Redis miss) must return the same payload as the cache.
-- signal_key: what the trend is ("term:#lowercase" / "cluster:<uuid>"). Trend
--   ids are derived from it, so the same trend keeps its id across cycles,
--   which campaign_draft_block's FK needs.
--
-- Rows are upserted each cycle and unranked trends get rank NULL instead of
-- the doc's TRUNCATE + rewrite: TRUNCATE can't run while campaign_draft_block
-- rows reference trend_ranking.
ALTER TABLE trend_ranking ADD COLUMN IF NOT EXISTS detail JSONB;
ALTER TABLE trend_ranking ADD COLUMN IF NOT EXISTS signal_key TEXT;
CREATE INDEX IF NOT EXISTS trend_ranking_rank_idx ON trend_ranking (rank) WHERE rank IS NOT NULL;
