import sqlite3

import pytest

from mixxxlib.errors import MixxxRunningError
from mixxxlib.safety import guard


def test_probe_lock_passes_on_idle_database(sandbox):
    conn = sqlite3.connect(sandbox.db, timeout=0.2, autocommit=True)
    guard.probe_lock(conn)
    assert not conn.in_transaction


def test_probe_lock_refuses_while_another_connection_holds_exclusive(sandbox):
    holder = sqlite3.connect(sandbox.db, autocommit=True)
    holder.execute("BEGIN EXCLUSIVE")
    conn = sqlite3.connect(sandbox.db, timeout=0.2, autocommit=True)
    with pytest.raises(MixxxRunningError) as e:
        guard.probe_lock(conn)
    assert e.value.details == {"reason": "locked"}
    holder.execute("ROLLBACK")


def test_assert_mixxx_closed_uses_process_check_first(sandbox):
    conn = sqlite3.connect(sandbox.db, timeout=0.2, autocommit=True)
    with pytest.raises(MixxxRunningError) as e:
        guard.assert_mixxx_closed(conn, process_check=lambda: True)
    assert e.value.details == {"reason": "process"}
    guard.assert_mixxx_closed(conn, process_check=lambda: None)  # unknown → fall through to the lock probe


def test_process_check_returns_none_without_pgrep(monkeypatch):
    monkeypatch.setattr(guard.shutil, "which", lambda name: None)
    assert guard.mixxx_process_running() is None
