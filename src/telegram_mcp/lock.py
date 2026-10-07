"""A small cross-process lock for the single Telegram update consumer."""

from __future__ import annotations

import os
from pathlib import Path


class InstanceLock:
    """An exclusive lockfile; deliberately fail rather than split update consumption."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self._held = False

    def acquire(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        try:
            descriptor = os.open(self.path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        except FileExistsError as error:
            raise RuntimeError(
                f"Another telegram-mcp process may already be running ({self.path}). "
                "Remove a stale lock only after verifying no server process is running."
            ) from error
        try:
            os.write(descriptor, f"{os.getpid()}\n".encode())
        finally:
            os.close(descriptor)
        self._held = True

    def release(self) -> None:
        if not self._held:
            return
        try:
            self.path.unlink()
        except FileNotFoundError:
            pass
        self._held = False
