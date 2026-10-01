"""Serialize inventory writers, including nested publication calls."""

import fcntl
from contextlib import contextmanager
from contextvars import ContextVar

_held = ContextVar('maintenance_locks', default=frozenset())


@contextmanager
def writer_lock(base_dir):
    path = (base_dir / 'temp/maintenance.lock').resolve()
    if path in _held.get():
        yield
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('a') as stream:
        try:
            fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise RuntimeError('Another maintenance writer is active') from exc
        token = _held.set(_held.get() | {path})
        try:
            yield
        finally:
            _held.reset(token)
            fcntl.flock(stream, fcntl.LOCK_UN)
