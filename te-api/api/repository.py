"""te-api's Postgres side.

API-2: the fallback read of trend_ranking on a cache miss. Never recomputes
anything: te-aggregator already stored each trend's full payload in
trend_ranking.detail (migration 003).

Campaigns: the trend lookup and the campaign_draft_block insert."""
import json
import logging
from typing import Dict, Optional

import psycopg2

from api.cache import version_of

logger = logging.getLogger(__name__)


class Repository:
    def __init__(self, host, port, user, password, dbname):
        self._dsn = dict(host=host, port=port, user=user, password=password, dbname=dbname)
        self._conn = None

    def close(self):
        if self._conn is not None:
            self._conn.close()
            self._conn = None

    def _query(self, sql, args=(), fetch=True):
        for attempt in (1, 2):
            try:
                if self._conn is None or self._conn.closed:
                    self._conn = psycopg2.connect(**self._dsn)
                with self._conn, self._conn.cursor() as cur:
                    cur.execute(sql, args)
                    return cur.fetchall() if fetch else None
            except (psycopg2.OperationalError, psycopg2.InterfaceError):
                self.close()
                if attempt == 2:
                    raise
                logger.warning("postgres connection lost; reconnecting")

    def ranked_feed(self) -> Optional[Dict]:
        """The current ranking, in the same shape as the cached payload, or None
        if nothing has been ranked yet."""
        rows = self._query("SELECT detail, computed_at FROM trend_ranking"
                           " WHERE rank IS NOT NULL AND detail IS NOT NULL ORDER BY rank")
        if not rows:
            return None
        computed_at = max(r[1] for r in rows)
        trends = [r[0] if isinstance(r[0], dict) else json.loads(r[0]) for r in rows]
        return {"version": version_of(computed_at), "computedAt": computed_at.strftime("%Y-%m-%dT%H:%M:%SZ"),
                "trends": trends}

    def trend_detail(self, trend_id: str) -> Optional[Dict]:
        """A trend's stored payload, ranked or not (a draft can be made from a trend
        that just dropped out of the ranking), or None if it doesn't exist."""
        rows = self._query("SELECT detail FROM trend_ranking WHERE id = %s AND detail IS NOT NULL", (trend_id,))
        if not rows:
            return None
        return rows[0][0] if isinstance(rows[0][0], dict) else json.loads(rows[0][0])

    def insert_draft_block(self, block_id: str, trend_id: str, campaign_id: str, suggested_copy: str) -> None:
        self._query("INSERT INTO campaign_draft_block (id, trend_id, mailchimp_campaign_id, suggested_copy, status)"
                    " VALUES (%s, %s, %s, %s, 'draft')", (block_id, trend_id, campaign_id, suggested_copy),
                    fetch=False)
