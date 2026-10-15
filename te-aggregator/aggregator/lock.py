"""AGG-1 single-flight lock: SET key token NX PX ttl to acquire; release only
if we still hold it (compare-and-delete in one Lua call, so we can't delete a
lock that expired and was re-taken by another instance)."""
import uuid

_RELEASE = "if redis.call('get', KEYS[1]) == ARGV[1] then return redis.call('del', KEYS[1]) else return 0 end"


class SingleFlightLock:
    def __init__(self, redis, key: str = "trends:aggregator:lock", ttl_ms: int = 120_000):
        self.redis, self.key, self.ttl_ms = redis, key, ttl_ms
        self._token = None

    def acquire(self) -> bool:
        token = uuid.uuid4().hex
        if self.redis.set(self.key, token, px=self.ttl_ms, nx=True):
            self._token = token
            return True
        return False

    def release(self) -> bool:
        if self._token is None:
            return False
        released = self.redis.eval(_RELEASE, keys=[self.key], args=[self._token]) == 1
        self._token = None
        return released

    def __enter__(self):
        return self.acquire()

    def __exit__(self, *exc):
        self.release()
        return False
