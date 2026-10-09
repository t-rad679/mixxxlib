from __future__ import annotations

from typing import Annotated, Optional

import typer

from mixxxlib import output
from mixxxlib.cli.common import command, global_options, open_library
from mixxxlib.settings import select

app = typer.Typer(no_args_is_help=True, help="Mixxx settings (mixxx.cfg) introspection.")


@app.command("show")
@global_options
def show(ctx: typer.Context,
         selector: Annotated[Optional[str], typer.Argument(metavar="[SECTION[.KEY]]", help="Section or section.key.")] = None) -> None:
    """Print mixxx.cfg, one section, or one value."""
    with command(ctx, "settings show") as out:
        lib = open_library(ctx)
        value = select(lib.settings, selector)
        data = {"selector": selector, "value": value}
        text = output.kv(value) if isinstance(value, dict) else str(value)
        out.emit(data, warnings=lib.read_warnings(), text=text)
