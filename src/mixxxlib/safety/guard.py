"""Refuse to mutate while Mixxx is running (spec §7.1, §7.3 step 3)."""

from __future__ import annotations

import shutil
import sqlite3
import subprocess
from collections.abc import Callable

from mixxxlib.errors import MixxxRunningError

PROCESS_NAME = "mixxx"


def mixxx_process_running() -> bool | None:
    """Best-effort `pgrep -x mixxx`. None when pgrep is unavailable or fails."""
    if shutil.which("pgrep") is None:
        return None
    try:
        result = subprocess.run(["pgrep", "-x", PROCESS_NAME], capture_output=True, timeout=5, check=False)
    except (OSError, subprocess.TimeoutExpired):
        return None
    return result.returncode == 0


def is_locked_error(exc: sqlite3.OperationalError) -> bool:
    name = getattr(exc, "sqlite_errorname", None) or ""
    text = str(exc).lower()
    return name in ("SQLITE_BUSY", "SQLITE_LOCKED") or "locked" in text or "busy" in text


def probe_lock(conn: sqlite3.Connection) -> None:
    """BEGIN IMMEDIATE then ROLLBACK: catches another connection that is mid-write-transaction
    right now. This relies on journal_mode=delete, which is true of every Mixxx database
    mixxxlib has ever been pointed at but is not something mixxxlib configures -- it never
    creates a database, so it never sets journal_mode. Under delete mode an exclusive writer
    blocks readers too, which is what makes this probe meaningful at all.

    It is not the primary defense against the real hazard, and is not authoritative on its own:
    a running Mixxx holds an in-memory track cache that can silently overwrite an external edit
    even when no write transaction happens to be open at this exact instant, so a Mixxx that
    merely has the file open passes this probe cleanly. That hazard is covered by the
    pgrep-based process check in assert_mixxx_closed, which runs first, not by this probe.
    """
    try:
        conn.execute("BEGIN IMMEDIATE")
    except sqlite3.OperationalError as exc:
        if is_locked_error(exc):
            raise MixxxRunningError(
                "the database is locked by another connection (is Mixxx running?)", reason="locked"
            ) from None
        raise
    conn.execute("ROLLBACK")


def assert_mixxx_closed(
    conn: sqlite3.Connection, *, process_check: Callable[[], bool | None] = mixxx_process_running
) -> None:
    if process_check() is True:
        raise MixxxRunningError("Mixxx is running; close it before changing the library", reason="process")
    probe_lock(conn)
