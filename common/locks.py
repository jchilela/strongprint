from contextlib import contextmanager

import redis
from django.conf import settings

_client: redis.Redis | None = None


def redis_client() -> redis.Redis:
    global _client
    if _client is None:
        _client = redis.Redis.from_url(settings.REDIS_URL)
    return _client


@contextmanager
def printer_lock(printer_id, *, timeout: int = 3600, blocking_timeout: float = 5):
    """Um trabalho de cada vez por impressora (o contador tem de ser só deste trabalho).

    Devolve True se obteve o lock, False caso contrário.
    """
    lock = redis_client().lock(
        f"strongprint:printer:{printer_id}", timeout=timeout, blocking_timeout=blocking_timeout
    )
    acquired = lock.acquire()
    try:
        yield acquired
    finally:
        if acquired:
            try:
                lock.release()
            except redis.exceptions.LockError:
                pass
