from __future__ import annotations

import os
from pathlib import Path
from typing import Annotated, Optional

import typer

from mixxxlib import output
from mixxxlib.cli.common import YesOption, command, global_options, open_library, require_yes
from mixxxlib.errors import NotFoundError

app = typer.Typer(no_args_is_help=True, help="Database backups and tag snapshots.")


@app.command("list")
@global_options
def list_(ctx: typer.Context) -> None:
    """Backups and tag snapshots with size and age, newest first."""
    with command(ctx, "backup list") as out:
        lib = open_library(ctx)
        entries = lib.backups.list()
        out.emit({"backup_dir": str(lib.paths.backup_dir), "backups": entries}, text=lambda: output.render_backups(entries))


@app.command("create")
@global_options
def create(ctx: typer.Context, label: Annotated[Optional[str], typer.Option("--label", help="Replaces the command name in the file name.")] = None) -> None:
    """Manual VACUUM INTO backup."""
    with command(ctx, "backup create") as out:
        lib = open_library(ctx)
        path = lib.backups.create(label)
        data = {"backup": str(path), "size": os.path.getsize(path)}
        out.emit(data, warnings=lib.read_warnings(), text=lambda: output.kv(data))


@app.command("prune")
@global_options
def prune(ctx: typer.Context) -> None:
    """Apply the retention rule now (TTL 30 days, floor of 10, per series)."""
    with command(ctx, "backup prune") as out:
        lib = open_library(ctx)
        removed = [str(p) for p in lib.backups.prune()]
        out.emit({"removed": removed, "retention": {"ttl_days": lib.retention.ttl_days, "keep_min": lib.retention.keep_min}},
                 text=lambda: "\n".join(["removed:"] + [f"  {p}" for p in removed]) if removed else "nothing to prune")


@app.command("restore")
@global_options
def restore(ctx: typer.Context, file: Annotated[Path, typer.Argument(metavar="FILE")], yes: YesOption = False) -> None:
    """Replace the database with FILE after backing up the current one. Needs --yes.

    Same shape as every other mutating command (spec: resolve -> require_yes -> mutate): FILE's
    existence is checked first, so a typo'd path is exit 3 without needing --yes, and only a real
    file without --yes falls through to the exit-8 confirmation gate.
    """
    with command(ctx, "backup restore") as out:
        lib = open_library(ctx)
        candidate = Path(file)
        if not candidate.is_file():
            raise NotFoundError(f"backup file not found at {candidate}", path=str(candidate))
        require_yes(yes, "backup restore")
        data = lib.backups.restore(candidate)
        out.emit(data, text=lambda: output.kv(data))
