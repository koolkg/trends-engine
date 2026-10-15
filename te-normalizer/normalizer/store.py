"""NORM-5: Postgres writer.

Every item gets a raw_content_item row (the archive, always written). Relevant
items also get a normalized_content_item row, and that INSERT is the dedup
check: ON CONFLICT (dedup_hash) DO NOTHING returns no row when an identical
post already exists, so "duplicate" is decided atomically by the unique index
rather than by a racy read-then-write. Both rows are one transaction.
"""
import logging
from dataclasses import dataclass
from datetime import datetime
from typing import Dict, Optional

import psycopg2

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class SaveResult:
    raw_id: int
    raw_ingested_at: datetime
    normalized_id: Optional[int]  # None: not relevant, or a duplicate of an existing post
    processed_at: Optional[datetime]


class Store:
    def __init__(self, host, port, user, password, dbname):
        self._dsn = dict(host=host, port=port, user=user, password=password, dbname=dbname)
        self._conn = None
        self._source_ids: Dict[str, int] = {}

    def connect(self):
        self._conn = psycopg2.connect(**self._dsn)
        logger.info("connected to postgres at %s:%s/%s", self._dsn["host"], self._dsn["port"], self._dsn["dbname"])

    def close(self):
        if self._conn is not None:
            self._conn.close()
            self._conn = None

    def _source_id(self, source_key: str, source_type: str) -> int:
        """sourceId (natural key, e.g. src_x_search) -> source.id, created on first
        sight. Committed on its own, so the cached id never points at a row from
        a transaction that later rolled back."""
        if source_key in self._source_ids:
            return self._source_ids[source_key]
        with self._conn, self._conn.cursor() as cur:
            cur.execute(
                "INSERT INTO source (source_key, source_type) VALUES (%s, %s) "
                "ON CONFLICT (source_key) DO NOTHING",
                (source_key, source_type),
            )
            cur.execute("SELECT id FROM source WHERE source_key = %s", (source_key,))
            source_id = cur.fetchone()[0]
        self._source_ids[source_key] = source_id
        return source_id

    def save(self, item, cleaned_text, dedup_hash, relevant, relevance_score, category) -> SaveResult:
        try:
            return self._save(item, cleaned_text, dedup_hash, relevant, relevance_score, category)
        except (psycopg2.OperationalError, psycopg2.InterfaceError):
            logger.warning("postgres connection lost, reconnecting and retrying once")
            self.close()
            self.connect()
            return self._save(item, cleaned_text, dedup_hash, relevant, relevance_score, category)

    def _save(self, item, cleaned_text, dedup_hash, relevant, relevance_score, category) -> SaveResult:
        if self._conn is None:
            self.connect()
        source_id = self._source_id(item.source_id, item.source_type)
        with self._conn, self._conn.cursor() as cur:  # one transaction; rolls back on error
            cur.execute(
                "INSERT INTO raw_content_item (source_id, external_content_id, raw_text, author_handle,"
                " posted_at, engagement_likes, engagement_shares, engagement_comments)"
                " VALUES (%s, %s, %s, %s, %s, %s, %s, %s) RETURNING id, ingested_at",
                (source_id, item.content_id, item.raw_text, item.author, item.posted_at,
                 item.likes, item.shares, item.comments),
            )
            raw_id, raw_ingested_at = cur.fetchone()
            normalized_id = processed_at = None
            if relevant:
                cur.execute(
                    "INSERT INTO normalized_content_item (raw_content_id, raw_content_ingested_at,"
                    " cleaned_text, dedup_hash, is_relevant, relevance_score, category)"
                    " VALUES (%s, %s, %s, %s, TRUE, %s, %s)"
                    " ON CONFLICT (dedup_hash) DO NOTHING RETURNING id, processed_at",
                    (raw_id, raw_ingested_at, cleaned_text, dedup_hash, relevance_score, category),
                )
                row = cur.fetchone()
                if row is not None:
                    normalized_id, processed_at = row
        return SaveResult(raw_id, raw_ingested_at, normalized_id, processed_at)
