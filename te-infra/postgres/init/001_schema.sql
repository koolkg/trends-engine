-- Trends Engine schema — mirrors the design's data model. Applied once, automatically, by the official
-- postgres image's entrypoint on first container init (see README for
-- what "once" means in practice).

CREATE EXTENSION IF NOT EXISTS vector;

-- ---------------------------------------------------------------------
-- Source — the design doc's ER diagram draws this with only "id (PK)"
-- and never shows its CREATE TABLE; filling that gap here rather than
-- leaving raw_content_item.source_id with nothing to reference. The
-- natural key (source_key) is what content.raw.ingested's "sourceId"
-- field carries (e.g. "src_tiktok_hashtag") — te-normalizer resolves it
-- to this table's surrogate id.
-- ---------------------------------------------------------------------

CREATE TABLE source (
  id SERIAL PRIMARY KEY,
  source_key TEXT NOT NULL UNIQUE,
  source_type TEXT NOT NULL,
  created_at TIMESTAMPTZ DEFAULT now()
);

-- ---------------------------------------------------------------------
-- High-volume, append-only
-- ---------------------------------------------------------------------

CREATE TABLE raw_content_item (
  id BIGSERIAL,
  source_id INT NOT NULL REFERENCES source(id),
  external_content_id TEXT NOT NULL,
  raw_text TEXT NOT NULL,
  author_handle TEXT,
  posted_at TIMESTAMPTZ,
  engagement_likes INT DEFAULT 0,
  engagement_shares INT DEFAULT 0,
  engagement_comments INT DEFAULT 0,
  ingested_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  -- A unique constraint on a partitioned table must include the partition
  -- key, so the PK is (id, ingested_at), not id alone — and anything
  -- referencing this table has to reference both columns.
  PRIMARY KEY (id, ingested_at)
) PARTITION BY RANGE (ingested_at);

-- PoC: one catch-all partition. Real day-partitioning is a production
-- concern (a PoC limitation), not needed at demo volume.
CREATE TABLE raw_content_item_default PARTITION OF raw_content_item DEFAULT;

CREATE TABLE normalized_content_item (
  id BIGSERIAL PRIMARY KEY,
  raw_content_id BIGINT NOT NULL,
  raw_content_ingested_at TIMESTAMPTZ NOT NULL,
  cleaned_text TEXT NOT NULL,
  dedup_hash TEXT NOT NULL,
  is_relevant BOOLEAN NOT NULL,
  relevance_score NUMERIC(4,3),
  category TEXT,
  processed_at TIMESTAMPTZ DEFAULT now(),
  FOREIGN KEY (raw_content_id, raw_content_ingested_at)
    REFERENCES raw_content_item (id, ingested_at)
);
CREATE UNIQUE INDEX ON normalized_content_item (dedup_hash);

-- time-series, one row per term per window
CREATE TABLE term_frequency_snapshot (
  id BIGSERIAL PRIMARY KEY,
  term TEXT NOT NULL,
  window_start TIMESTAMPTZ NOT NULL,
  window_end TIMESTAMPTZ NOT NULL,
  frequency INT NOT NULL,
  baseline_frequency NUMERIC(10,2),
  z_score NUMERIC(6,2)
);
CREATE INDEX ON term_frequency_snapshot (term, window_start DESC);

-- ---------------------------------------------------------------------
-- Small, hot, cached
-- ---------------------------------------------------------------------

CREATE TABLE content_cluster_assignment (
  content_id BIGINT NOT NULL REFERENCES normalized_content_item(id),
  cluster_id UUID NOT NULL,
  similarity_score NUMERIC(4,3),
  assigned_at TIMESTAMPTZ DEFAULT now(),
  PRIMARY KEY (content_id, cluster_id)
);

-- One row per active cluster, not per document (Low Level Details tab) —
-- what keeps the HNSW index small.
CREATE TABLE cluster_centroid (
  cluster_id UUID PRIMARY KEY,
  centroid VECTOR(384) NOT NULL,
  member_count BIGINT NOT NULL DEFAULT 1,
  last_assigned_at TIMESTAMPTZ DEFAULT now(),
  created_at TIMESTAMPTZ DEFAULT now()
);
CREATE INDEX ON cluster_centroid
  USING hnsw (centroid vector_cosine_ops)
  WITH (m = 16, ef_construction = 64);

-- rewritten wholesale every aggregation cycle, not upserted row by row
CREATE TABLE trend_ranking (
  id UUID PRIMARY KEY,
  label TEXT NOT NULL,
  category TEXT NOT NULL,
  velocity_score NUMERIC(6,2),
  rank INT,
  computed_at TIMESTAMPTZ DEFAULT now()
);

CREATE TABLE campaign_draft_block (
  id UUID PRIMARY KEY,
  trend_id UUID NOT NULL REFERENCES trend_ranking(id),
  mailchimp_campaign_id TEXT,
  suggested_copy TEXT,
  status TEXT DEFAULT 'draft',
  marketer_id UUID,
  created_at TIMESTAMPTZ DEFAULT now()
);
