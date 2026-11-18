"""A small cross-process lock for the single Telegram update consumer."""

from __future__ import annotations

import fcntl
import os
from pathlib import Path


class InstanceLock:
    """An exclusive kernel-held lockfile for one telegram-mcp instance.

    Exclusion lives in an ``fcntl.flock`` on a persistent lock file, so the OS
    releases the lock when the process dies for any reason (SIGKILL, terminal
    shutdown, WSL teardown). The file itself only stores the holder PID for
    diagnostics: a leftover file from a killed process never blocks the next
    start, and the file is intentionally never unlinked (unlinking would let
    a waiter lock a different inode).
    """

    def __init__(self, path: Path) -> None:
        self.path = path
        self._descriptor: int | None = None

    def acquire(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        descriptor = os.open(self.path, os.O_CREAT | os.O_RDWR, 0o600)
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as error:
            holder_pid = self._holder_pid(descriptor)
            os.close(descriptor)
            raise RuntimeError(f"Another telegram-mcp process is already running (pid {holder_pid}).") from error
        try:
            os.ftruncate(descriptor, 0)
            os.write(descriptor, f"{os.getpid()}\n".encode())
        except OSError:
            pass
        self._descriptor = descriptor

    def release(self) -> None:
        if self._descriptor is None:
            return
        try:
            fcntl.flock(self._descriptor, fcntl.LOCK_UN)
        finally:
            os.close(self._descriptor)
            self._descriptor = None

    @staticmethod
    def _holder_pid(descriptor: int) -> str:
        try:
            os.lseek(descriptor, 0, os.SEEK_SET)
            raw = os.read(descriptor, 64)
        except OSError:
            return "unknown"
        return raw.decode(errors="replace").strip() or "unknown"
