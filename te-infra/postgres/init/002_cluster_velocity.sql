-- te-cluster's growth scores (Phase 2b, CLU-5/CLU-6): one row per cluster per
-- window, mirroring term_frequency_snapshot so te-aggregator merges two
-- z-scores on equal footing (design doc, "Cluster velocity, made comparable
-- to a term's z-score"). The design doc has no table for this; it's added here.
--
-- Idempotent: runs automatically on a fresh volume (after 001), and can be
-- applied to an existing database with `make migrate`.
CREATE TABLE IF NOT EXISTS cluster_velocity_snapshot (
  id BIGSERIAL PRIMARY KEY,
  cluster_id UUID NOT NULL,
  window_start TIMESTAMPTZ NOT NULL,
  window_end TIMESTAMPTZ NOT NULL,
  frequency INT NOT NULL,
  baseline_frequency NUMERIC(10,2),
  z_score NUMERIC(6,2)
);
CREATE INDEX IF NOT EXISTS cluster_velocity_snapshot_cluster_window_idx
  ON cluster_velocity_snapshot (cluster_id, window_start DESC);
