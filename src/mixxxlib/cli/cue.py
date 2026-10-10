from __future__ import annotations

from typing import Annotated, Optional

import typer

from mixxxlib import output
from mixxxlib.cli.common import (
    DryRunOption, PathsOption, YesOption, command, global_options, open_library, require_yes, resolve_track,
)
from mixxxlib.errors import ValidationError
from mixxxlib.model import Track, parse_color

app = typer.Typer(no_args_is_help=True, help="Cue points: hotcue, loop, jump, intro, outro are writable; main/n60db/beat are read-only.")

TrackArg = Annotated[str, typer.Argument(metavar="TRACK")]
CueArg = Annotated[int, typer.Argument(metavar="CUE", help="Cue id.")]
TypeOption = Annotated[Optional[str], typer.Option("--type", metavar="TYPE", help="hotcue, loop, jump, intro, outro (or the integer).")]
PositionOption = Annotated[Optional[float], typer.Option("--position", metavar="SECONDS")]
PositionSamplesOption = Annotated[Optional[float], typer.Option("--position-samples", metavar="N", help="Interleaved stereo samples.")]
LengthOption = Annotated[Optional[float], typer.Option("--length", metavar="SECONDS")]
LengthSamplesOption = Annotated[Optional[float], typer.Option("--length-samples", metavar="N")]
SlotOption = Annotated[Optional[int], typer.Option("--slot", metavar="N", help="0-based hotcue slot.")]
LabelOption = Annotated[Optional[str], typer.Option("--label", metavar="TEXT")]
ColorOption = Annotated[Optional[str], typer.Option("--color", metavar="C", help="#RRGGBB, 0xRRGGBB, decimal or AARRGGBB (alpha stripped). Must be non-zero.")]


def _samples(track: Track, seconds: float | None, samples: float | None, what: str, *, required: bool) -> float | None:
    if seconds is not None and samples is not None:
        raise ValidationError(f"pass either --{what} or --{what}-samples, not both")
    if seconds is None and samples is None:
        if required:
            raise ValidationError(f"--{what} or --{what}-samples is required")
        return None
    if samples is not None:
        return float(samples)
    rate = track.samples_per_second
    if not rate:
        raise ValidationError(f"track {track.id} has no samplerate/channels; pass --{what}-samples", track_id=track.id)
    return float(seconds) * rate


@app.command("list")
@global_options
def list_(ctx: typer.Context, track: TrackArg, type_: TypeOption = None, paths: PathsOption = False) -> None:
    """Cues on a track, sorted by type, slot, position."""
    with command(ctx, "cue list") as out:
        lib = open_library(ctx)
        obj, decode_warnings = resolve_track(lib, track, paths=paths)
        cues = lib.cues.list(obj, type=type_)
        out.emit(cues, warnings=[*lib.read_warnings(), *decode_warnings], text=lambda: output.render_cues(cues))


@app.command("add")
@global_options
def add(ctx: typer.Context, track: TrackArg, type_: TypeOption = None, position: PositionOption = None,
        position_samples: PositionSamplesOption = None, color: ColorOption = None, length: LengthOption = None,
        length_samples: LengthSamplesOption = None, slot: SlotOption = None, label: LabelOption = None,
        paths: PathsOption = False, dry_run: DryRunOption = False) -> None:
    """Validated insert. --slot is required for hotcue, optional for loop, refused otherwise."""
    with command(ctx, "cue add") as out:
        lib = open_library(ctx)
        if type_ is None:
            raise ValidationError("--type is required")
        if color is None:
            raise ValidationError("--color is required (Mixxx discards cues whose colour is 0)")
        obj, decode_warnings = resolve_track(lib, track, paths=paths)
        pos = _samples(obj, position, position_samples, "position", required=True)
        length_value = _samples(obj, length, length_samples, "length", required=False) or 0.0
        result = lib.cues.add(obj, type_, pos, parse_color(color), slot=slot, length=length_value, label=label or "", dry_run=dry_run)
        out.emit(result.data, warnings=[*lib.read_warnings(), *decode_warnings], mutation=result,
                 text=lambda: output.render_cues([result.data["cue"]]))


@app.command("set")
@global_options
def set_(ctx: typer.Context, cue: CueArg, position: PositionOption = None, position_samples: PositionSamplesOption = None,
         length: LengthOption = None, length_samples: LengthSamplesOption = None, slot: SlotOption = None,
         label: LabelOption = None, color: ColorOption = None, dry_run: DryRunOption = False) -> None:
    """Update a cue; the same validation as add."""
    with command(ctx, "cue set") as out:
        lib = open_library(ctx)
        cue_obj, track_obj = lib.cues.get(cue)
        result = lib.cues.set(cue_obj, track_obj,
                              position_samples=_samples(track_obj, position, position_samples, "position", required=False),
                              length_samples=_samples(track_obj, length, length_samples, "length", required=False),
                              slot=slot, label=label, color=parse_color(color) if color is not None else None, dry_run=dry_run)
        out.emit(result.data, mutation=result, text=lambda: output.render_cues([result.data["cue"]]))


@app.command("delete")
@global_options
def delete(ctx: typer.Context, cue: CueArg, yes: YesOption = False, dry_run: DryRunOption = False) -> None:
    """Delete one cue. Needs --yes."""
    with command(ctx, "cue delete") as out:
        lib = open_library(ctx)
        cue_obj, _ = lib.cues.get(cue)
        if not dry_run:
            require_yes(yes, "cue delete")
        result = lib.cues.delete(cue_obj, dry_run=dry_run)
        out.emit(result.data, mutation=result, text=lambda: f"deleted cue {cue_obj.id}")


@app.command("clear")
@global_options
def clear(ctx: typer.Context, track: TrackArg, type_: TypeOption = None, paths: PathsOption = False,
          yes: YesOption = False, dry_run: DryRunOption = False) -> None:
    """Delete every writable-type cue on a track, or only one writable type. Read-only types are never touched."""
    with command(ctx, "cue clear") as out:
        lib = open_library(ctx)
        obj, decode_warnings = resolve_track(lib, track, paths=paths)
        if not dry_run:
            require_yes(yes, "cue clear")
        result = lib.cues.clear(obj, type=type_, dry_run=dry_run)
        out.emit(result.data, warnings=[*lib.read_warnings(), *decode_warnings], mutation=result,
                 text=lambda: f"deleted {len(result.data['deleted'])} cue(s)")
