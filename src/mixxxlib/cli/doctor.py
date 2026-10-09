from __future__ import annotations

import typer

from mixxxlib import output
from mixxxlib.cli.common import command, global_options, open_library
from mixxxlib.doctor import summarize


@global_options
def doctor(ctx: typer.Context) -> None:
    """Pure-read health check. Exit 0 when clean, 10 when any error-severity finding exists."""
    with command(ctx, "doctor") as out:
        lib = open_library(ctx)
        findings = lib.doctor()
        summary = summarize(findings)
        out.emit({"findings": findings, "summary": summary}, warnings=lib.read_warnings(),
                 text=lambda: output.render_findings(findings) + "\n" + output.kv(summary))
        if summary["errors"]:
            raise typer.Exit(10)
