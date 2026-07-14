"""Redis-backed fixed-window rate limiter (shared across API processes/workers).

Design: INCR on riq:rl:{scope}:{key}:{window} with an expiry — atomic, O(1), and safe
under concurrency. Fails OPEN on Redis outages (availability over strictness; the event
is logged and counted) so a cache blip can't take the API down with it.
"""

import time

import redis

from relayiq.logging_setup import get_logger
from relayiq.observability.metrics import RATE_LIMITED

log = get_logger("ratelimit")


class RateLimiter:
    def __init__(self, client: redis.Redis | None = None):
        self._client = client

    @property
    def r(self) -> redis.Redis:
        if self._client is None:
            from relayiq.services.cache import get_redis

            self._client = get_redis()
        return self._client

    def allow(self, scope: str, key: str, limit: int, window_seconds: int = 60) -> bool:
        """True when the caller is within `limit` events per window. limit<=0 disables."""
        if limit <= 0:
            return True
        window = int(time.time() // window_seconds)
        redis_key = f"riq:rl:{scope}:{key}:{window}"
        try:
            pipe = self.r.pipeline()
            pipe.incr(redis_key)
            pipe.expire(redis_key, window_seconds + 1)
            count, _ = pipe.execute()
        except redis.RedisError:
            log.warning("rate limiter unavailable — failing open", scope=scope)
            return True
        if int(count) > limit:
            RATE_LIMITED.labels(scope=scope).inc()
            return False
        return True


_limiter: RateLimiter | None = None


def get_rate_limiter() -> RateLimiter:
    global _limiter
    if _limiter is None:
        _limiter = RateLimiter()
    return _limiter


def reset_rate_limiter(client: redis.Redis | None = None) -> None:
    """Test hook."""
    global _limiter
    _limiter = RateLimiter(client)
