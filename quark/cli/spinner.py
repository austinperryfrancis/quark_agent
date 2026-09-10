"""Tiny dependency-free terminal spinner for interactive model work."""

from __future__ import annotations

import sys
import threading
from collections.abc import Iterator
from contextlib import contextmanager


@contextmanager
def spinner(
    label: str = "Quark is thinking", *, enabled: bool = True
) -> Iterator[None]:
    """Animate while work runs, and stay silent for non-interactive output."""
    if not enabled or not sys.stdout.isatty():
        yield
        return
    stopped = threading.Event()

    def animate() -> None:
        frames = "⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏"
        index = 0
        while not stopped.wait(0.08):
            sys.stdout.write(f"\r{frames[index % len(frames)]} {label}…")
            sys.stdout.flush()
            index += 1

    worker = threading.Thread(target=animate, daemon=True)
    worker.start()
    try:
        yield
    finally:
        stopped.set()
        worker.join(timeout=0.2)
        sys.stdout.write("\r" + " " * (len(label) + 5) + "\r")
        sys.stdout.flush()
