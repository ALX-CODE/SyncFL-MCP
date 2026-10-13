"""Atomic, correlated, cross-process serialized FL controller transport helpers."""

from __future__ import annotations

import json
import os
import threading
import time
import uuid
from contextlib import contextmanager
from pathlib import Path

_thread_lock = threading.RLock()


def poll_delay(elapsed: float) -> float:
    """Wait between checks of a local file or lock, scaled to how long we've waited.

    The controller answers a ping in about 2 ms. A fixed 20 ms sleep made every
    call cost ~23 ms. Check every 0.2 ms for the first 25 ms (a ping then costs
    ~3 ms), every 1 ms up to 250 ms, then every 5 ms so long operations cost
    almost no CPU. Never zero: a zero sleep would busy-spin.
    """
    if elapsed < 0.025:
        return 0.0002
    return 0.001 if elapsed < 0.25 else 0.005


def atomic_json(path: Path, value: dict) -> None:
    temp = path.with_name(path.name + "." + uuid.uuid4().hex + ".tmp")
    try:
        temp.write_text(json.dumps(value, allow_nan=False), encoding="utf-8")
        deadline = time.monotonic() + 1
        while True:
            try:
                os.replace(temp, path)
                break
            except PermissionError:
                # Windows readers/AV can briefly prevent file replacement. This
                # is before the MIDI trigger; retrying file publication cannot
                # repeat a DAW operation. A failed publication never dispatches.
                if time.monotonic() >= deadline:
                    raise
                time.sleep(0.02)
    finally:
        temp.unlink(missing_ok=True)


@contextmanager
def transaction_lock(path: Path, timeout: float):
    """OS-owned lock; automatically released if a client crashes."""
    started = time.monotonic()
    deadline = started + timeout
    if not _thread_lock.acquire(timeout=timeout):
        raise TimeoutError("FL bridge is busy")
    stream = None
    locked = False
    try:
        stream = path.open("a+b")
        stream.seek(0, 2)
        if stream.tell() == 0:
            stream.write(b"0")
            stream.flush()
        while not locked:
            try:
                stream.seek(0)
                if os.name == "nt":
                    import msvcrt

                    msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
                else:
                    import fcntl

                    fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
                locked = True
            except OSError:
                if time.monotonic() >= deadline:
                    raise TimeoutError("Another FL client holds the bridge lock") from None
                time.sleep(min(poll_delay(time.monotonic() - started),
                               max(0.0, deadline - time.monotonic())))
        yield
    finally:
        if stream is not None:
            if locked:
                stream.seek(0)
                if os.name == "nt":
                    import msvcrt

                    msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
                else:
                    import fcntl

                    fcntl.flock(stream, fcntl.LOCK_UN)
            stream.close()
        _thread_lock.release()
