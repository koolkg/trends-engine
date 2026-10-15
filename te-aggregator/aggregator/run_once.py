"""Run one aggregation cycle now and print the ranking (make -C te-aggregator rank).

Uses the same config and single-flight lock as the service, so it is safe to
run while te-aggregator is up: if a cycle is already in progress this one is
skipped and prints nothing."""
from aggregator.config import load_config
from aggregator.cycle import Aggregator
from aggregator.lock import SingleFlightLock
from aggregator.redis_client import Redis
from aggregator.repository import Repository


def main() -> None:
    c = load_config()
    repo = Repository(c.postgres_host, c.postgres_port, c.postgres_user, c.postgres_password, c.postgres_db)
    redis = Redis(c.redis_host, c.redis_port)
    try:
        agg = Aggregator(repo, redis, SingleFlightLock(redis), c.top_n, c.lookback_hours, c.sparkline_hours,
                         c.decay_per_hour, c.min_z, c.spike_z, c.cache_ttl_seconds)
        trends = agg.run("manual") or []
        if not trends:
            print("no trends ranked (no scores in the lookback window, or another cycle held the lock)")
        for t in trends:
            print(f"{t['rank']:2}. {t['name']}  ({t['category']}, score {t['velocityScore']}, {t['velocityLabel']})")
    finally:
        repo.close()
        redis.close()


if __name__ == "__main__":
    main()
