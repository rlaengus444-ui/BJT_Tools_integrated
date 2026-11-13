"""Cooperative, thread-local training cancellation; never an invalid geometry.

Like asyncio.CancelledError this bypasses broad Exception handlers which turn
malformed geometry into reports. Callers must discard the unfinished episode.
"""
from contextlib import contextmanager
from contextvars import ContextVar
import time


class Cancelled(BaseException):
    pass


_event = ContextVar('powdernest_training_cancel', default=None)
_progress = ContextVar('powdernest_validation_progress', default=None)


def check_cancelled():
    event = _event.get()
    if event is not None and event.is_set():
        raise Cancelled()


def report_progress(phase_label, *, force=False, **details):
    context = _progress.get()
    if context is None: return
    callback, last = context; now = time.monotonic()
    if not force and now - last[0] < .3: return
    last[0] = now
    callback({'phase_label': phase_label, **details})


@contextmanager
def progress_scope(callback):
    token = _progress.set((callback, [0.]))
    try: yield
    finally: _progress.reset(token)


@contextmanager
def cancellation_scope(event):
    token = _event.set(event)
    try:
        check_cancelled()
        yield
    finally:
        _event.reset(token)
