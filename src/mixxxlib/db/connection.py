"""Connections and the mutation protocol (spec §7.2, §7.3).

Every mutating command, in this order: schema guard → Mixxx-running guard → validate inside a read
transaction → dry-run exit → VACUUM INTO backup → file tags → one BEGIN IMMEDIATE transaction →
PRAGMA integrity_check → prune backups.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Callable
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Iterator, Protocol

from mixxxlib.db import schema as schema_mod
from mixxxlib.errors import DatabaseError, MixxxRunningError, MixxxlibError, NotFoundError, TagError
from mixxxlib.model import MutationResult, WarningMessage
from mixxxlib.safety import backup as backup_mod
from mixxxlib.safety.guard import assert_mixxx_closed, is_locked_error, mixxx_process_running, probe_lock

BUSY_TIMEOUT_SECONDS = 1.0


def _uri(path: Path, mode: str) -> str:
    return f"{Path(path).resolve().as_uri()}?mode={mode}"


def _require_file(path: Path) -> None:
    if not Path(path).is_file():
        raise NotFoundError(f"database not found at {path}", path=str(path))


def open_ro(path: Path, *, timeout: float = BUSY_TIMEOUT_SECONDS) -> sqlite3.Connection:
    """A connection that is incapable of mutation (spec §7.2)."""
    _require_file(path)
    conn = sqlite3.connect(_uri(path, "ro"), uri=True, timeout=timeout, autocommit=True)
    conn.row_factory = sqlite3.Row
    return conn


def open_rw(path: Path, *, timeout: float = BUSY_TIMEOUT_SECONDS) -> sqlite3.Connection:
    _require_file(path)
    conn = sqlite3.connect(_uri(path, "rw"), uri=True, timeout=timeout, autocommit=True)
    conn.row_factory = sqlite3.Row
    return conn


def translate(exc: sqlite3.Error) -> MixxxlibError:
    if isinstance(exc, sqlite3.OperationalError) and is_locked_error(exc):
        return MixxxRunningError("the database is locked by another connection (is Mixxx running?)", reason="locked")
    return DatabaseError(f"sqlite error: {exc}", sqlite=type(exc).__name__)


@contextmanager
def translating_errors() -> Iterator[None]:
    try:
        yield
    except sqlite3.Error as exc:
        raise translate(exc) from exc


# ---------------------------------------------------------------- statements

@dataclass(frozen=True)
class Deferred:
    """A bound parameter whose value is known only after the file step (e.g. the file's new mtime)."""

    key: str


@dataclass(frozen=True)
class Statement:
    sql: str
    params: tuple[Any, ...] = ()


def quote_literal(value: Any) -> str:
    if value is None:
        return "NULL"
    if isinstance(value, Deferred):
        return f"<{value.key}>"
    if isinstance(value, bool):
        return "1" if value else "0"
    if isinstance(value, (int, float)):
        return repr(value)
    if isinstance(value, bytes):
        return f"X'{value.hex()}'"
    return "'" + str(value).replace("'", "''") + "'"


def render_statement(statement: Statement) -> str:
    parts = statement.sql.split("?")
    if len(parts) - 1 != len(statement.params):
        raise ValueError(f"{len(statement.params)} params for {len(parts) - 1} placeholders in {statement.sql!r}")
    out = parts[0]
    for param, rest in zip(statement.params, parts[1:]):
        out += quote_literal(param) + rest
    return out


def _resolve(param: Any, results: dict[str, Any]) -> Any:
    if isinstance(param, Deferred):
        if param.key not in results:
            raise DatabaseError(f"deferred parameter {param.key!r} was never produced by a file action", key=param.key)
        return results[param.key]
    return param


# ---------------------------------------------------------------- plans

class FileAction(Protocol):
    def describe(self) -> str: ...
    def apply(self) -> dict[str, Any]: ...
    def rollback(self) -> None: ...


@dataclass
class MutationPlan:
    statements: list[Statement] = field(default_factory=list)
    data: dict[str, Any] = field(default_factory=dict)
    warnings: list[WarningMessage] = field(default_factory=list)
    file_actions: list[FileAction] = field(default_factory=list)
    after_commit: Callable[[sqlite3.Connection], dict[str, Any]] | None = None


def integrity_check(conn: sqlite3.Connection) -> str:
    rows = [row[0] for row in conn.execute("PRAGMA integrity_check")]
    return "ok" if rows == ["ok"] else "; ".join(rows)


def _try_rollback(action: FileAction) -> bool:
    try:
        action.rollback()
        return True
    except Exception:  # best effort; the error we are already raising matters more
        return False


def run_mutation(
    conn: sqlite3.Connection,
    *,
    label: str,
    backup_dir: Path,
    build: Callable[[sqlite3.Connection], MutationPlan],
    schema_ceiling: int,
    requires: str | None = None,
    dry_run: bool = False,
    retention: backup_mod.Retention | None = None,
    process_check: Callable[[], bool | None] | None = None,
    now: datetime | None = None,
) -> MutationResult:
    """Run spec §7.3 steps 2–11 around `build`, which validates and returns the plan."""
    # 2. schema guard. Probe the lock first: schema_info() swallows sqlite3.Error into a "missing"
    # verdict, which would otherwise misreport a locked database as an unreadable schema.
    probe_lock(conn)
    info = schema_mod.schema_info(conn, ceiling=schema_ceiling)
    warnings = list(schema_mod.require_schema(info, capability=requires))
    # 3. Mixxx-running guard
    assert_mixxx_closed(conn, process_check=process_check or mixxx_process_running)
    # 4. validate inside a read transaction; nothing is written yet
    with translating_errors():
        conn.execute("BEGIN")
        try:
            plan = build(conn)
        finally:
            conn.execute("ROLLBACK")
    warnings.extend(plan.warnings)
    rendered = [render_statement(s) for s in plan.statements]
    file_ops = [a.describe() for a in plan.file_actions]
    # 6. dry run
    if dry_run:
        return MutationResult(data=plan.data, statements=rendered, backup=None, integrity=None, dry_run=True,
                              warnings=warnings, file_operations=file_ops)
    # 7. backup, only when the database will be written
    backup_path: Path | None = None
    if plan.statements:
        backup_path = backup_mod.create_backup(conn, backup_dir, label, now=now)
    # 8. file tags first
    results: dict[str, Any] = {}
    applied: list[FileAction] = []
    try:
        for action in plan.file_actions:
            results.update(action.apply())
            applied.append(action)
    except TagError:
        for action in reversed(applied):
            _try_rollback(action)
        raise
    # 9. one transaction
    integrity: str | None = None
    if plan.statements:
        try:
            conn.execute("BEGIN IMMEDIATE")
            for statement in plan.statements:
                conn.execute(statement.sql, tuple(_resolve(p, results) for p in statement.params))
            conn.execute("COMMIT")
        except (sqlite3.Error, DatabaseError) as exc:
            try:
                conn.execute("ROLLBACK")
            except sqlite3.Error:
                pass
            restored = [a.describe() for a in reversed(applied) if _try_rollback(a)]
            err = exc if isinstance(exc, DatabaseError) else translate(exc)
            err.details.update(backup=str(backup_path), file_rollback=restored)
            raise err from exc
        # 10. verify
        integrity = integrity_check(conn)
        if integrity != "ok":
            raise DatabaseError(f"integrity_check failed after commit: {integrity}",
                                backup=str(backup_path), integrity=integrity)
        # 11. prune
        backup_mod.prune(backup_dir, retention or backup_mod.Retention.from_env(), now=now)
    data = plan.after_commit(conn) if plan.after_commit is not None else plan.data
    return MutationResult(data=data, statements=rendered, backup=str(backup_path) if backup_path else None,
                          integrity=integrity, dry_run=False, warnings=warnings, file_operations=file_ops)
