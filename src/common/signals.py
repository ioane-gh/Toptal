"""A process-wide stop flag for graceful SIGINT/SIGTERM handling.

runner.py installs the handlers; ingest_b2b's chunk generators and
ingest_reseller's file-dispatch loop check STOP_EVENT so a Ctrl-C stops
scheduling new work while whatever is already in flight finishes and
commits normally (see README "Design decisions" for the scope of this --
in-process chunk loops stop between chunks; ProcessPoolExecutor workers
already dispatched are not interrupted mid-file, only no new files are
handed out).
"""
from __future__ import annotations

import signal
import threading

STOP_EVENT = threading.Event()


def install_signal_handlers() -> None:
    def _handler(signum, frame):  # noqa: ARG001
        STOP_EVENT.set()

    signal.signal(signal.SIGINT, _handler)
    signal.signal(signal.SIGTERM, _handler)
