from __future__ import annotations

import sys
from typing import Annotated

import typer

from mixxxlib import __version__

app = typer.Typer(
    add_completion=False,
    rich_markup_mode=None,
    pretty_exceptions_enable=False,
    no_args_is_help=True,
)


@app.callback(invoke_without_command=True)
def root(
    version: Annotated[bool, typer.Option("--version", help="Print the version and exit.")] = False,
) -> None:
    if version:
        typer.echo(__version__)
        raise typer.Exit(0)


def main(argv: list[str] | None = None) -> int:
    try:
        app(args=argv if argv is not None else sys.argv[1:], standalone_mode=False)
    except typer.Exit as exc:
        return int(exc.exit_code)
    return 0
