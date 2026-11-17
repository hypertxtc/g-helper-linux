# SPDX-License-Identifier: GPL-3.0-or-later

"""Serial background task runner for D-Bus and subprocess work.

asusd handles EC writes (fan curves, PPT, profile changes) that can take
hundreds of milliseconds, and cardwire/kscreen are subprocesses.  Running
those on the GTK main thread makes the window stutter or freeze, so every
backend operation goes through this single worker queue and its result is
delivered back on the main loop with ``GLib.idle_add``.

One worker is intentional: it keeps backend operations ordered (write then
refresh) and avoids concurrent D-Bus writes racing on the EC.
"""

from __future__ import annotations

import logging
import queue
import threading
from typing import Any, Callable

from .deps import GLib

log = logging.getLogger(__name__)

Callback = Callable[[Any], None]


def _invoke(callback: Callback, value: Any) -> bool:
    try:
        callback(value)
    except Exception:  # noqa: BLE001 - never let a UI callback kill the loop
        log.exception("backend callback failed")
    return False


class TaskRunner:
    def __init__(self) -> None:
        self._queue: queue.Queue = queue.Queue()
        self._closed = False
        self._thread = threading.Thread(
            target=self._loop, name="ghelper-backend", daemon=True
        )
        self._thread.start()

    def submit(
        self,
        work: Callable[[], Any],
        *,
        done: Callback | None = None,
        error: Callback | None = None,
    ) -> None:
        if not self._closed:
            self._queue.put((work, done, error))

    def _loop(self) -> None:
        while True:
            item = self._queue.get()
            if item is None:
                return
            work, done, error = item
            try:
                result = work()
            except Exception as exc:  # noqa: BLE001 - delivered to the UI
                if error is not None:
                    GLib.idle_add(_invoke, error, exc)
                else:
                    log.warning("background task failed: %s", exc)
            else:
                if done is not None:
                    GLib.idle_add(_invoke, done, result)

    def shutdown(self) -> None:
        if self._closed:
            return
        self._closed = True
        self._queue.put(None)
