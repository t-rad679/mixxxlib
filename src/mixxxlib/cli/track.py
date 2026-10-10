from __future__ import annotations

from typing import Annotated, Optional

import typer

from mixxxlib import output
from mixxxlib.cli.common import (
    DryRunOption, PathsOption, YesOption, command, global_options, open_library, require_yes, resolve_crate,
    resolve_track,
)
from mixxxlib.db import tracks as tracks_sql
from mixxxlib.db.tracks import FILE_BACKED_FIELDS
from mixxxlib.errors import ValidationError

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


@app.command("set")
@global_options
def set_(ctx: typer.Context, track: Annotated[str, typer.Argument(metavar="TRACK")],
         assignments: Annotated[Optional[list[str]], typer.Argument(metavar="[FIELD=VALUE...]")] = None,
         clear: Annotated[Optional[list[str]], typer.Option(
             "--clear", metavar="FIELD", help="Set NULL in the database and remove the file tag.")] = None,
         mixxx_only: Annotated[bool, typer.Option("--mixxx-only", help="Write only the database.")] = False,
         file_only: Annotated[bool, typer.Option("--file-only", help="Write only the audio file.")] = False,
         paths: PathsOption = False, yes: YesOption = False, dry_run: DryRunOption = False) -> None:
    """Write metadata to the audio file and the database (file first). --yes is needed when the file is written."""
    with command(ctx, "track set") as out:
        lib = open_library(ctx)
        if mixxx_only and file_only:
            raise ValidationError("--mixxx-only and --file-only exclude each other")
        fields: dict[str, str] = {}
        for item in assignments or []:
            name, sep, value = item.partition("=")
            if not sep or not name:
                raise ValidationError(f"expected FIELD=VALUE, got {item!r}", value=item)
            fields[name] = value
        clears = clear or []
        obj, decode_warnings = resolve_track(lib, track, paths=paths)
        # Validate before the confirmation gate: bad input is exit 4, even without --yes — the
        # destructive-write confirmation must not block on arguments that were never going to work.
        for name, raw in fields.items():
            tracks_sql.coerce_field(name, raw)
        for name in clears:
            if name not in tracks_sql.WRITABLE_FIELDS:
                raise ValidationError(f"unknown field {name!r}", field=name, fields=sorted(tracks_sql.WRITABLE_FIELDS))
        overlap = set(fields) & set(clears)
        if overlap:
            raise ValidationError(f"fields both set and cleared: {sorted(overlap)}", fields=sorted(overlap))
        if not fields and not clears:
            raise ValidationError("nothing to set: pass FIELD=VALUE or --clear FIELD")
        touches_file = not mixxx_only and bool((set(fields) | set(clears)) & FILE_BACKED_FIELDS)
        if touches_file and not dry_run:
            require_yes(yes, "track set (writes the audio file)")
        result = lib.tracks.set(obj, fields, clear=clears, file=not mixxx_only, db=not file_only, dry_run=dry_run)
        changed = output.as_json(result.data["changed"])
        out.emit(result.data, warnings=[*lib.read_warnings(), *decode_warnings], mutation=result,
                 text=lambda: output.kv({"changed": changed, "file_written": result.data["file_written"]}))
