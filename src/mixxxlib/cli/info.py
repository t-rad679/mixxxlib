from __future__ import annotations

import typer

from mixxxlib import output
from mixxxlib.cli.common import command, global_options, open_library


@global_options
def info(ctx: typer.Context) -> None:
    """Resolved paths, schema verdict, whether Mixxx is running, backups, version. Pure read."""
    with command(ctx, "info") as out:
        lib = open_library(ctx)
        data = lib.info()
        out.emit(data, warnings=lib.read_warnings(), text=lambda: output.kv(data))
