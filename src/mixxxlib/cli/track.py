from __future__ import annotations

from typing import Annotated, Optional

import typer

from mixxxlib import output
from mixxxlib.cli.common import PathsOption, command, global_options, open_library, resolve_crate, resolve_track

app = typer.Typer(no_args_is_help=True, help="Track metadata, analysis summary and cues.")


@app.command("list")
@global_options
def list_(ctx: typer.Context,
          crate: Annotated[Optional[str], typer.Option("--crate", metavar="CRATE", help="Only tracks in this crate.")] = None,
          by_id: Annotated[bool, typer.Option("--id", help="CRATE is an integer id.")] = False,
          parent: Annotated[Optional[str], typer.Option("--parent", metavar="CRATE", help="Disambiguate CRATE by its parent (schema >= 41).")] = None,
          recursive: Annotated[bool, typer.Option("--recursive", help="Include descendant crates' tracks (schema >= 41).")] = False,
          search: Annotated[Optional[str], typer.Option("--search", metavar="TEXT", help="Case-insensitive match on artist, title or album.")] = None) -> None:
    """List tracks, sorted by id."""
    with command(ctx, "track list") as out:
        lib = open_library(ctx)
        crate_obj = resolve_crate(lib, crate, by_id=by_id, parent=parent) if crate else None
        tracks, decode_warnings = lib.tracks.list(crate=crate_obj, search=search, recursive=recursive)
        out.emit(tracks, warnings=[*lib.read_warnings(), *decode_warnings], text=lambda: output.render_tracks(tracks))


@app.command("show")
@global_options
def show(ctx: typer.Context, track: Annotated[str, typer.Argument(metavar="TRACK")], paths: PathsOption = False) -> None:
    """One track with its analysis summary and cues."""
    with command(ctx, "track show") as out:
        lib = open_library(ctx)
        obj, decode_warnings = resolve_track(lib, track, paths=paths)
        data = {"track": obj, "beatgrid": obj.analysis.beatgrid, "cues": []}
        out.emit(data, warnings=[*lib.read_warnings(), *decode_warnings], text=lambda: output.render_track(obj))
