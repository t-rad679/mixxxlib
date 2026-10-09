"""Root Typer app. Thin shell over mixxxlib.library; maps errors to exit codes in exactly one place (common.command/main)."""

from __future__ import annotations

import os
import sys
import traceback
from typing import Annotated, Optional

import typer
# Typer 0.27 vendors its own Click fork (`typer._click`) rather than using the top-level `click`
# package at runtime: exceptions raised during argument parsing/invocation under
# `standalone_mode=False` are instances of that fork's classes, not `click.*` (verified on this
# machine: `click.UsageError` catches none of them). `typer.TyperException` is the public base
# every one of those (`UsageError`, `NoArgsIsHelpError`, `BadOptionUsage`, `NoSuchOption`, ...)
# shares, and it already carries `.exit_code`/`.format_message()` — no need to reach into the
# private `typer._click` module at all. `typer.Exit` and `typer.Abort` are *not*
# `TyperException` subclasses (verified) and are handled separately below.
from mixxxlib import __version__
from mixxxlib.cli import info as info_cmd, settings as settings_cmd, track as track_cmd
from mixxxlib.cli.common import Globals, emit_error
from mixxxlib.errors import InternalError, UsageError

app = typer.Typer(
    name="mixxxlib",
    add_completion=False,
    rich_markup_mode=None,
    pretty_exceptions_enable=False,
    no_args_is_help=True,
    help="Offline reader/writer for a Mixxx library. Never run while Mixxx is open.",
)


def _version(value: bool) -> None:
    if value:
        typer.echo(__version__)
        raise typer.Exit(0)


@app.callback()
def root(
    ctx: typer.Context,
    settings_path: Annotated[Optional[str], typer.Option("--settings-path", metavar="DIR", help="Mixxx settings directory.")] = None,
    db: Annotated[Optional[str], typer.Option("--db", metavar="FILE", help="Database file.")] = None,
    backup_dir: Annotated[Optional[str], typer.Option("--backup-dir", metavar="DIR", help="Where backups go.")] = None,
    json_: Annotated[bool, typer.Option("--json", help="Machine-readable output.")] = False,
    version: Annotated[bool, typer.Option("--version", callback=_version, is_eager=True, help="Print the version and exit.")] = False,
) -> None:
    ctx.obj = Globals(settings_path=settings_path, db=db, backup_dir=backup_dir, json=json_)


app.command("info")(info_cmd.info)
app.add_typer(settings_cmd.app, name="settings")
app.add_typer(track_cmd.app, name="track")


def main(argv: list[str] | None = None) -> int:
    """Run the app and return a process exit code; never lets an exception escape (spec §4).

    With `standalone_mode=False`, a `typer.Exit` raised anywhere during parsing or invocation
    (our own commands via `common.command`, or `--version`) is swallowed by Typer/Click and
    surfaces as `app(...)`'s *return value*, not as a raised exception — so a plain `None`/non-int
    return means success (exit 0) and an int return is the exit code to use directly.

    A `typer.TyperException` (covers `UsageError`, `NoArgsIsHelpError`, `BadOptionUsage`,
    `NoSuchOption`, ...) is re-raised instead, because it happens during argument parsing, before
    any of our own commands run — this is Click's own usage-error path, so its `exit_code` (2 for
    the `UsageError` family, matching spec §4's "Click's default"; the `TyperException` base
    default is 1) is used as-is rather than remapped.
    """
    args = list(sys.argv[1:] if argv is None else argv)
    json_mode = "--json" in args
    try:
        rv = app(args=args, standalone_mode=False, prog_name="mixxxlib")
    except typer.Abort:
        return 1
    except typer.TyperException as exc:
        if json_mode:
            err_cls = UsageError if exc.exit_code == 2 else InternalError
            emit_error(err_cls(exc.format_message()), True)
        else:
            exc.show()
        return exc.exit_code
    except Exception as exc:  # last-resort guard: never let a raw traceback escape (spec §4, decision 6)
        if os.environ.get("MIXXXLIB_DEBUG"):
            traceback.print_exc()
        emit_error(InternalError(f"{type(exc).__name__}: {exc}"), json_mode)
        return 1
    return rv if isinstance(rv, int) else 0
