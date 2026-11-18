import os
import subprocess
import sys
from pathlib import Path

import pytest

from telegram_mcp.lock import InstanceLock

CHILD_HOLDER = """
import sys
import time
from pathlib import Path

from telegram_mcp.lock import InstanceLock

lock = InstanceLock(Path(sys.argv[1]))
lock.acquire()
print("held", flush=True)
time.sleep(600)
"""


def test_lock_records_holder_pid_and_allows_reacquire(tmp_path: Path) -> None:
    path = tmp_path / "telegram-mcp.sqlite.lock"
    lock = InstanceLock(path)
    lock.acquire()
    try:
        assert path.read_text().strip() == str(os.getpid())
    finally:
        lock.release()
    second = InstanceLock(path)
    second.acquire()
    second.release()


def test_lock_excludes_concurrent_holder(tmp_path: Path) -> None:
    first = InstanceLock(tmp_path / "telegram-mcp.sqlite.lock")
    first.acquire()
    try:
        with pytest.raises(RuntimeError, match=rf"already running \(pid {os.getpid()}\)"):
            InstanceLock(first.path).acquire()
    finally:
        first.release()


def test_stale_leftover_lock_file_does_not_block_start(tmp_path: Path) -> None:
    path = tmp_path / "telegram-mcp.sqlite.lock"
    path.write_text("92721\n")
    lock = InstanceLock(path)
    lock.acquire()
    try:
        assert path.read_text().strip() == str(os.getpid())
    finally:
        lock.release()


def test_lock_is_released_when_holder_is_killed(tmp_path: Path) -> None:
    path = tmp_path / "telegram-mcp.sqlite.lock"
    child = subprocess.Popen(
        [sys.executable, "-c", CHILD_HOLDER, str(path)],
        stdout=subprocess.PIPE,
        text=True,
    )
    try:
        assert child.stdout is not None
        assert child.stdout.readline().strip() == "held"
        with pytest.raises(RuntimeError, match="already running"):
            InstanceLock(path).acquire()
        child.kill()
        child.wait(timeout=10)
    finally:
        if child.poll() is None:
            child.kill()
            child.wait(timeout=10)
    lock = InstanceLock(path)
    lock.acquire()
    lock.release()
