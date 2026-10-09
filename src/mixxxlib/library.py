"""The public API façade (spec §10.1). Never imports Typer."""

from __future__ import annotations

import os
import sqlite3
from collections.abc import Callable, Iterable, Iterator, Mapping
from contextlib import closing, contextmanager
from typing import Any

from mixxxlib import __version__, paths as paths_mod
from mixxxlib.db import connection as conn_mod, crates as crates_sql, schema as schema_mod
from mixxxlib.model import Crate, CrateNode, MutationResult, SchemaInfo, WarningMessage
from mixxxlib.safety import backup as backup_mod
from mixxxlib.safety.guard import mixxx_process_running
from mixxxlib.settings import load_mixxx_cfg


class Library:
    def __init__(self, resolved: paths_mod.ResolvedPaths, *, schema_ceiling: int,
                 environ: Mapping[str, str] | None = None,
                 process_check: Callable[[], bool | None] | None = None) -> None:
        self.paths = resolved
        self.schema_ceiling = schema_ceiling
        self._environ = os.environ if environ is None else environ
        self._process_check = process_check or mixxx_process_running
        self.retention = backup_mod.Retention.from_env(self._environ)
        with closing(conn_mod.open_ro(resolved.db)) as conn, conn_mod.translating_errors():
            self.schema: SchemaInfo = schema_mod.schema_info(conn, ceiling=schema_ceiling)
        self.crates = CratesAPI(self)

    @classmethod
    def open(cls, settings_path: str | None = None, db: str | None = None, backup_dir: str | None = None, *,
             schema_ceiling: int | None = None, environ: Mapping[str, str] | None = None,
             process_check: Callable[[], bool | None] | None = None) -> "Library":
        resolved = paths_mod.resolve(settings_path, db, backup_dir, environ=environ)
        ceiling = schema_mod.SCHEMA_CEILING if schema_ceiling is None else schema_ceiling
        return cls(resolved, schema_ceiling=ceiling, environ=environ, process_check=process_check)

    # -- schema / state -------------------------------------------------------------

    @property
    def nested(self) -> bool:
        return "nested_crates" in self.schema.capabilities

    def require_nested(self) -> None:
        schema_mod.require_schema(self.schema, capability="nested_crates")

    def mixxx_running(self) -> bool | None:
        return self._process_check()

    def read_warnings(self) -> list[WarningMessage]:
        if self.mixxx_running() is True:
            return [WarningMessage("mixxx_running", "Mixxx is running; values may be stale", {})]
        return []

    # -- connections ----------------------------------------------------------------

    @contextmanager
    def read(self) -> Iterator[sqlite3.Connection]:
        with closing(conn_mod.open_ro(self.paths.db)) as conn, conn_mod.translating_errors():
            yield conn

    def mutate(self, label: str, build: Callable[[sqlite3.Connection], conn_mod.MutationPlan], *,
               dry_run: bool = False, requires: str | None = None) -> MutationResult:
        with closing(conn_mod.open_rw(self.paths.db)) as conn:
            return conn_mod.run_mutation(
                conn, label=label, backup_dir=self.paths.backup_dir, build=build, schema_ceiling=self.schema_ceiling,
                requires=requires, dry_run=dry_run, retention=self.retention, process_check=self._process_check,
            )

    # -- settings / info ------------------------------------------------------------

    @property
    def settings(self) -> dict[str, dict[str, str]]:
        return load_mixxx_cfg(self.paths.config)

    def info(self) -> dict[str, Any]:
        backups = backup_mod.list_backups(self.paths.backup_dir)
        return {
            "version": __version__,
            "paths": {"settings_dir": str(self.paths.settings_dir), "db": str(self.paths.db),
                      "config": str(self.paths.config), "config_exists": self.paths.config.is_file(),
                      "backup_dir": str(self.paths.backup_dir)},
            "schema": {"version": self.schema.version, "ceiling": self.schema.ceiling,
                       "capabilities": sorted(self.schema.capabilities), "verdict": self.schema.verdict,
                       "message": self.schema.message},
            "mixxx_running": self.mixxx_running(),
            "backups": {"count": len(backups), "newest": backups[0].name if backups else None},
        }


class CratesAPI:
    def __init__(self, lib: Library) -> None:
        self._lib = lib

    def list(self, *, parent: Crate | None = None, roots_only: bool = False) -> list[Crate]:
        if parent is not None:
            self._lib.require_nested()
        with self._lib.read() as conn:
            return crates_sql.list_crates(conn, nested=self._lib.nested, parent_id=parent.id if parent else None,
                                          roots_only=roots_only)

    def get(self, selector: str | int, *, by_id: bool = False, parent: Crate | None = None) -> Crate:
        if parent is not None:
            self._lib.require_nested()
        with self._lib.read() as conn:
            if by_id:
                return crates_sql.get_crate_by_id(conn, int(selector), nested=self._lib.nested)
            return crates_sql.find_crate_by_name(conn, str(selector), nested=self._lib.nested,
                                                 parent_id=parent.id if parent else None, parent_given=parent is not None)

    def tree(self) -> list[CrateNode]:
        with self._lib.read() as conn:
            return crates_sql.tree(conn, nested=self._lib.nested)

    def subtree_ids(self, crate: Crate, *, recursive: bool) -> list[int]:
        if not recursive:
            return [crate.id]
        self._lib.require_nested()
        with self._lib.read() as conn:
            return [crate.id, *crates_sql.descendant_ids(conn, crate.id)]

    def members(self, crate: Crate, *, recursive: bool = False) -> list[int]:
        ids = self.subtree_ids(crate, recursive=recursive)
        with self._lib.read() as conn:
            found: set[int] = set()
            for cid in ids:
                found |= crates_sql.member_ids(conn, cid)
        return sorted(found)

    def create(self, name: str, *, parent: Crate | None = None, dry_run: bool = False) -> MutationResult:
        nested = self._lib.nested
        return self._lib.mutate("crate create", lambda c: crates_sql.plan_create(c, name, parent=parent, nested=nested),
                                dry_run=dry_run, requires="nested_crates" if parent is not None else None)

    def rename(self, crate: Crate, new_name: str, *, dry_run: bool = False) -> MutationResult:
        nested = self._lib.nested
        return self._lib.mutate("crate rename", lambda c: crates_sql.plan_rename(c, crate, new_name, nested=nested), dry_run=dry_run)

    def move(self, crate: Crate, *, parent: Crate | None, dry_run: bool = False) -> MutationResult:
        return self._lib.mutate("crate move", lambda c: crates_sql.plan_move(c, crate, parent=parent),
                                dry_run=dry_run, requires="nested_crates")

    def update(self, crate: Crate, *, locked: bool | None = None, show: bool | None = None,
               autodj_source: bool | None = None, dry_run: bool = False) -> MutationResult:
        nested = self._lib.nested
        return self._lib.mutate("crate update", lambda c: crates_sql.plan_update(
            c, crate, locked=locked, show=show, autodj_source=autodj_source, nested=nested), dry_run=dry_run)

    def delete(self, crate: Crate, *, recursive: bool = False, dry_run: bool = False) -> MutationResult:
        nested = self._lib.nested
        return self._lib.mutate("crate delete", lambda c: crates_sql.plan_delete(c, crate, recursive=recursive, nested=nested),
                                dry_run=dry_run)

    def add(self, crate: Crate, track_ids: Iterable[int], *, dry_run: bool = False) -> MutationResult:
        nested, ids = self._lib.nested, list(track_ids)
        return self._lib.mutate("crate add", lambda c: crates_sql.plan_add(c, crate, ids, nested=nested), dry_run=dry_run)

    def remove(self, crate: Crate, track_ids: Iterable[int], *, dry_run: bool = False) -> MutationResult:
        nested, ids = self._lib.nested, list(track_ids)
        return self._lib.mutate("crate remove", lambda c: crates_sql.plan_remove(c, crate, ids, nested=nested), dry_run=dry_run)

    def set_tracks(self, crate: Crate, track_ids: Iterable[int], *, dry_run: bool = False) -> MutationResult:
        nested, ids = self._lib.nested, list(track_ids)
        return self._lib.mutate("crate set-tracks", lambda c: crates_sql.plan_set_tracks(c, crate, ids, nested=nested), dry_run=dry_run)

    def export_m3u8(self, crate: Crate, *, recursive: bool = False) -> str:
        ids = self.subtree_ids(crate, recursive=recursive)
        with self._lib.read() as conn:
            return crates_sql.export_m3u8(conn, ids)
