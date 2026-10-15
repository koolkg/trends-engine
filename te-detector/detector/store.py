"""DET-6 (persist): one term_frequency_snapshot row per emitted Score.
Only terms with count >= min_count are emitted, so this stays small."""
import logging
from typing import Sequence

import psycopg2
from psycopg2.extras import execute_values

logger = logging.getLogger(__name__)

Z_LIMIT = 9999.99            # z_score is NUMERIC(6,2)
BASELINE_LIMIT = 99999999.99  # baseline_frequency is NUMERIC(10,2)


def _clamp(value: float, limit: float) -> float:
    return max(-limit, min(limit, value))


class Store:
    def __init__(self, host, port, user, password, dbname):
        self._dsn = dict(host=host, port=port, user=user, password=password, dbname=dbname)
        self._conn = None

    def connect(self):
        self._conn = psycopg2.connect(**self._dsn)
        logger.info("connected to postgres at %s:%s/%s", self._dsn["host"], self._dsn["port"], self._dsn["dbname"])

    def close(self):
        if self._conn is not None:
            self._conn.close()
            self._conn = None

    def save(self, scores: Sequence) -> None:
        if not scores:
            return
        try:
            self._save(scores)
        except (psycopg2.OperationalError, psycopg2.InterfaceError):
            logger.warning("postgres connection lost, reconnecting and retrying once")
            self.close()
            self._save(scores)

    def _save(self, scores) -> None:
        if self._conn is None:
            self.connect()
        rows = [(s.term, s.window_start, s.window_end, s.frequency,
                 _clamp(s.baseline_frequency, BASELINE_LIMIT), _clamp(s.z_score, Z_LIMIT)) for s in scores]
        with self._conn, self._conn.cursor() as cur:
            execute_values(cur, "INSERT INTO term_frequency_snapshot"
                                " (term, window_start, window_end, frequency, baseline_frequency, z_score)"
                                " VALUES %s", rows)
