from __future__ import annotations

from typing import Annotated, Optional

import typer

from mixxxlib import output
from mixxxlib.cli.common import (
    DryRunOption, PathsOption, StdinOption, YesOption, command, global_options, open_library, read_selectors,
    require_yes, resolve_crate,
)
from mixxxlib.errors import ValidationError
from mixxxlib.library import Library

app = typer.Typer(no_args_is_help=True, help="Crate CRUD and membership. Crates are addressed by name, or by id with --id.")

CrateArg = Annotated[str, typer.Argument(metavar="CRATE", help="Crate name (or id with --id).")]
ByIdOption = Annotated[bool, typer.Option("--id", help="CRATE is an integer id.")]
ParentOption = Annotated[Optional[str], typer.Option("--parent", metavar="CRATE", help="Parent crate (schema >= 41).")]
RecursiveOption = Annotated[bool, typer.Option("--recursive", help="Include descendant crates (schema >= 41).")]
TracksArg = Annotated[Optional[list[str]], typer.Argument(metavar="[TRACK...]", help="Track ids (or paths with --paths).")]


def _track_ids(lib: Library, tracks: list[str] | None, *, paths: bool, use_stdin: bool) -> list[int]:
    selectors = read_selectors(tracks or [], use_stdin)
    return lib.tracks.resolve(selectors, paths=paths)


@app.command("list")
@global_options
def list_(ctx: typer.Context, parent: ParentOption = None,
          root: Annotated[bool, typer.Option("--root", help="Only root crates.")] = False) -> None:
    """All crates sorted by name; with nesting, optionally one level."""
    with command(ctx, "crate list") as out:
        lib = open_library(ctx)
        parent_crate = lib.crates.get(parent) if parent else None
        crates = lib.crates.list(parent=parent_crate, roots_only=root)
        out.emit(crates, warnings=lib.read_warnings(), text=lambda: output.render_crates(crates))


@app.command("show")
@global_options
def show(ctx: typer.Context, crate: CrateArg, by_id: ByIdOption = False, parent: ParentOption = None,
         tracks: Annotated[bool, typer.Option("--tracks", help="Include the crate's tracks.")] = False,
         recursive: RecursiveOption = False) -> None:
    """One crate, optionally with its tracks."""
    with command(ctx, "crate show") as out:
        lib = open_library(ctx)
        obj = resolve_crate(lib, crate, by_id=by_id, parent=parent)
        data: dict = {"crate": obj}
        if recursive:
            lib.require_nested()
        if tracks:
            data["tracks"], decode_warnings = lib.tracks.list(crate=obj, recursive=recursive)
        else:
            decode_warnings = []
        text = output.render_crate(obj) + ("\n\n" + output.render_tracks(data["tracks"]) if tracks else "")
        out.emit(data, warnings=[*lib.read_warnings(), *decode_warnings], text=text)


@app.command("tree")
@global_options
def tree(ctx: typer.Context) -> None:
    """The crate hierarchy (schema >= 41); a flat list of roots below."""
    with command(ctx, "crate tree") as out:
        lib = open_library(ctx)
        nodes = lib.crates.tree()
        out.emit(nodes, warnings=lib.read_warnings(), text=lambda: output.render_tree(nodes))


@app.command("create")
@global_options
def create(ctx: typer.Context, name: Annotated[str, typer.Argument(metavar="NAME")], parent: ParentOption = None,
           dry_run: DryRunOption = False) -> None:
    """Create a crate. A duplicate name among siblings is refused."""
    with command(ctx, "crate create") as out:
        lib = open_library(ctx)
        parent_crate = lib.crates.get(parent) if parent else None
        result = lib.crates.create(name, parent=parent_crate, dry_run=dry_run)
        out.emit(result.data, mutation=result, text=lambda: output.render_crate(result.data["crate"]))


@app.command("rename")
@global_options
def rename(ctx: typer.Context, crate: CrateArg, new_name: Annotated[str, typer.Argument(metavar="NEW_NAME")],
           by_id: ByIdOption = False, parent: ParentOption = None, dry_run: DryRunOption = False) -> None:
    """Rename a crate."""
    with command(ctx, "crate rename") as out:
        lib = open_library(ctx)
        result = lib.crates.rename(resolve_crate(lib, crate, by_id=by_id, parent=parent), new_name, dry_run=dry_run)
        out.emit(result.data, mutation=result, text=lambda: output.render_crate(result.data["crate"]))


@app.command("move")
@global_options
def move(ctx: typer.Context, crate: CrateArg, by_id: ByIdOption = False,
         parent: Annotated[Optional[str], typer.Option("--parent", metavar="CRATE", help="New parent crate.")] = None,
         root: Annotated[bool, typer.Option("--root", help="Move to the root.")] = False,
         dry_run: DryRunOption = False) -> None:
    """Re-parent a crate and its subtree (schema >= 41). Refuses a cycle."""
    with command(ctx, "crate move") as out:
        lib = open_library(ctx)
        if (parent is None) == (not root):
            raise ValidationError("pass exactly one of --parent CRATE or --root")
        lib.require_nested()
        obj = resolve_crate(lib, crate, by_id=by_id, parent=None)
        target = lib.crates.get(parent) if parent else None
        result = lib.crates.move(obj, parent=target, dry_run=dry_run)
        out.emit(result.data, mutation=result, text=lambda: output.render_crate(result.data["crate"]))


@app.command("update")
@global_options
def update(ctx: typer.Context, crate: CrateArg, by_id: ByIdOption = False, parent: ParentOption = None,
           locked: Annotated[Optional[bool], typer.Option("--locked/--no-locked")] = None,
           show_flag: Annotated[Optional[bool], typer.Option("--show/--no-show")] = None,
           autodj_source: Annotated[Optional[bool], typer.Option("--autodj-source/--no-autodj-source")] = None,
           dry_run: DryRunOption = False) -> None:
    """Set the locked / show / autodj_source flags."""
    with command(ctx, "crate update") as out:
        lib = open_library(ctx)
        obj = resolve_crate(lib, crate, by_id=by_id, parent=parent)
        result = lib.crates.update(obj, locked=locked, show=show_flag, autodj_source=autodj_source, dry_run=dry_run)
        out.emit(result.data, mutation=result, text=lambda: output.render_crate(result.data["crate"]))


@app.command("delete")
@global_options
def delete(ctx: typer.Context, crate: CrateArg, by_id: ByIdOption = False, parent: ParentOption = None,
           recursive: RecursiveOption = False, yes: YesOption = False, dry_run: DryRunOption = False) -> None:
    """Delete a crate and its membership rows. Needs --yes."""
    with command(ctx, "crate delete") as out:
        lib = open_library(ctx)
        obj = resolve_crate(lib, crate, by_id=by_id, parent=parent)
        if not dry_run:
            require_yes(yes, "crate delete")
        result = lib.crates.delete(obj, recursive=recursive, dry_run=dry_run)
        out.emit(result.data, mutation=result,
                 text=lambda: "\n".join(f"deleted {d['crate'].name} (id {d['crate'].id}, {d['track_count']} tracks)" for d in result.data["deleted"]))


@app.command("add")
@global_options
def add(ctx: typer.Context, crate: CrateArg, tracks: TracksArg = None, by_id: ByIdOption = False, parent: ParentOption = None,
        paths: PathsOption = False, stdin: StdinOption = False, dry_run: DryRunOption = False) -> None:
    """Add tracks to a crate (idempotent)."""
    with command(ctx, "crate add") as out:
        lib = open_library(ctx)
        obj = resolve_crate(lib, crate, by_id=by_id, parent=parent)
        result = lib.crates.add(obj, _track_ids(lib, tracks, paths=paths, use_stdin=stdin), dry_run=dry_run)
        out.emit(result.data, mutation=result, text=lambda: output.kv({"added": result.data["added"], "already_member": result.data["already_member"]}))


@app.command("remove")
@global_options
def remove(ctx: typer.Context, crate: CrateArg, tracks: TracksArg = None, by_id: ByIdOption = False, parent: ParentOption = None,
           paths: PathsOption = False, stdin: StdinOption = False, yes: YesOption = False, dry_run: DryRunOption = False) -> None:
    """Remove tracks from a crate (idempotent). Needs --yes."""
    with command(ctx, "crate remove") as out:
        lib = open_library(ctx)
        obj = resolve_crate(lib, crate, by_id=by_id, parent=parent)
        if not dry_run:
            require_yes(yes, "crate remove")
        result = lib.crates.remove(obj, _track_ids(lib, tracks, paths=paths, use_stdin=stdin), dry_run=dry_run)
        out.emit(result.data, mutation=result, text=lambda: output.kv({"removed": result.data["removed"], "not_member": result.data["not_member"]}))


@app.command("set-tracks")
@global_options
def set_tracks(ctx: typer.Context, crate: CrateArg, tracks: TracksArg = None, by_id: ByIdOption = False, parent: ParentOption = None,
               paths: PathsOption = False, stdin: StdinOption = False, yes: YesOption = False, dry_run: DryRunOption = False) -> None:
    """Make the crate's membership exactly this set. Needs --yes."""
    with command(ctx, "crate set-tracks") as out:
        lib = open_library(ctx)
        obj = resolve_crate(lib, crate, by_id=by_id, parent=parent)
        if not dry_run:
            require_yes(yes, "crate set-tracks")
        result = lib.crates.set_tracks(obj, _track_ids(lib, tracks, paths=paths, use_stdin=stdin), dry_run=dry_run)
        out.emit(result.data, mutation=result, text=lambda: output.kv({k: result.data[k] for k in ("added", "removed", "unchanged")}))


@app.command("export")
@global_options
def export(ctx: typer.Context, crate: CrateArg, by_id: ByIdOption = False, parent: ParentOption = None,
           fmt: Annotated[str, typer.Option("--format", help="Only m3u8 in v1.")] = "m3u8",
           recursive: RecursiveOption = False) -> None:
    """Write an #EXTM3U playlist with absolute paths to stdout (--json is ignored)."""
    with command(ctx, "crate export") as out:
        lib = open_library(ctx)
        if fmt != "m3u8":
            raise ValidationError(f"unsupported export format {fmt!r}; only m3u8 is available", format=fmt)
        obj = resolve_crate(lib, crate, by_id=by_id, parent=parent)
        out.emit_raw(lib.crates.export_m3u8(obj, recursive=recursive))
