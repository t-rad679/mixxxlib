"""Shared CLI plumbing: global options, the command context, exit-code mapping (spec §4, §8)."""

from __future__ import annotations

import functools
import inspect
import os
import sys
import traceback
from collections.abc import Callable, Iterable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Annotated, Any, Optional

import typer
# See the matching comment in cli/__init__.py: Typer 0.27 vendors its own Click fork, so a usage
# error escaping a command body (none currently raise one directly, but a future command might)
# is an instance of *this* module's `ClickException`, not the top-level `click` package's.
from typer._click import exceptions as click_exceptions

from mixxxlib import output
from mixxxlib.errors import ConfirmationRequiredError, InternalError, MixxxlibError, ValidationError
from mixxxlib.library import Library
from mixxxlib.model import MutationResult, WarningMessage


@dataclass
class Globals:
    settings_path: Optional[str] = None
    db: Optional[str] = None
    backup_dir: Optional[str] = None
    json: bool = False


GLOBAL_PARAMS = [
    inspect.Parameter("settings_path", inspect.Parameter.KEYWORD_ONLY, default=None,
                      annotation=Annotated[Optional[str], typer.Option("--settings-path", metavar="DIR", help="Mixxx settings directory.")]),
    inspect.Parameter("db", inspect.Parameter.KEYWORD_ONLY, default=None,
                      annotation=Annotated[Optional[str], typer.Option("--db", metavar="FILE", help="Database file (overrides --settings-path for the database).")]),
    inspect.Parameter("backup_dir", inspect.Parameter.KEYWORD_ONLY, default=None,
                      annotation=Annotated[Optional[str], typer.Option("--backup-dir", metavar="DIR", help="Where backups go.")]),
    inspect.Parameter("json_", inspect.Parameter.KEYWORD_ONLY, default=False,
                      annotation=Annotated[bool, typer.Option("--json", help="Machine-readable output.")]),
]

DryRunOption = Annotated[bool, typer.Option("--dry-run", help="Print the statements and change nothing.")]
YesOption = Annotated[bool, typer.Option("--yes", help="Confirm a destructive operation.")]
StdinOption = Annotated[bool, typer.Option("--stdin", help="Also read one selector per line from stdin.")]
PathsOption = Annotated[bool, typer.Option("--paths", help="Track selectors are file paths, not ids.")]


def get_globals(ctx: typer.Context) -> Globals:
    return ctx.ensure_object(Globals)


def global_options(fn: Callable[..., Any]) -> Callable[..., Any]:
    """Append the four global options to a command so they are accepted after the subcommand too.

    `eval_str=True` is required here: every `cli/*` module has `from __future__ import
    annotations`, so `fn`'s parameter annotations are plain strings until resolved. Typer's own
    parameter scanner resolves them the same way (`inspect.signature(callback, eval_str=True)`)
    for an undecorated command, but once we attach our own `__signature__` below, Typer's
    resolution no longer applies — a `Parameter.annotation` already baked into an explicit
    `__signature__` is used as-is, `eval_str` or not (verified on this machine). Skipping this
    would silently strip every `Annotated[..., typer.Argument(...)]` marker's "this is an
    Argument" metadata, as `typing.get_type_hints()` (Typer's fallback for plain annotations)
    drops `Annotated` extras by default — turning `settings show SELECTOR` into an unwanted
    `--selector` option.
    """
    signature = inspect.signature(fn, eval_str=True)
    params = [p for p in signature.parameters.values() if p.kind != inspect.Parameter.VAR_KEYWORD]

    @functools.wraps(fn)
    def wrapper(*args: Any, **kwargs: Any) -> Any:
        ctx = kwargs.get("ctx") or next(a for a in args if isinstance(a, typer.Context))
        g = get_globals(ctx)
        for name in ("settings_path", "db", "backup_dir"):
            value = kwargs.pop(name, None)
            if value is not None:
                setattr(g, name, value)
        if kwargs.pop("json_", False):
            g.json = True
        return fn(*args, **kwargs)

    wrapper.__signature__ = signature.replace(parameters=params + GLOBAL_PARAMS)  # type: ignore[attr-defined]
    return wrapper


def open_library(ctx: typer.Context) -> Library:
    g = get_globals(ctx)
    return Library.open(settings_path=g.settings_path, db=g.db, backup_dir=g.backup_dir)


def emit_error(err: MixxxlibError, json_mode: bool) -> None:
    if json_mode:
        print(output.dumps(err.to_payload()), file=sys.stderr)
    else:
        print(output.error_text(err), file=sys.stderr)


class Emitter:
    def __init__(self, command_name: str, json_mode: bool) -> None:
        self.command_name = command_name
        self.json_mode = json_mode

    def emit(self, data: Any, *, warnings: Iterable[WarningMessage] = (), mutation: MutationResult | None = None,
             text: str | Callable[[], str] | None = None) -> None:
        warning_list = list(warnings) + (list(mutation.warnings) if mutation is not None else [])
        if self.json_mode:
            print(output.dumps(output.envelope(self.command_name, data, warning_list, mutation)))
            return
        for warning in warning_list:
            print(f"mixxxlib: warning: {warning.code}: {warning.message}", file=sys.stderr)
        body = text() if callable(text) else text
        if body is None:
            rendered = output.as_json(data)
            body = output.kv(rendered) if isinstance(rendered, dict) else output.dumps(rendered)
        if body:
            print(body)
        if mutation is not None:
            print(output.render_mutation(mutation))

    def emit_raw(self, text: str) -> None:
        """For outputs that are not JSON even under --json (crate export)."""
        sys.stdout.write(text)


@contextmanager
def command(ctx: typer.Context, name: str) -> Iterator[Emitter]:
    json_mode = get_globals(ctx).json
    try:
        yield Emitter(name, json_mode)
    except MixxxlibError as err:
        emit_error(err, json_mode)
        raise typer.Exit(err.exit_code) from None
    except (typer.Exit, typer.Abort, click_exceptions.ClickException):
        raise
    except Exception as exc:  # exit 1: always a bug
        if os.environ.get("MIXXXLIB_DEBUG"):
            traceback.print_exc()
        emit_error(InternalError(f"{type(exc).__name__}: {exc}"), json_mode)
        raise typer.Exit(1) from None


def require_yes(yes: bool, action: str) -> None:
    if not yes:
        raise ConfirmationRequiredError(f"{action} is destructive; pass --yes to confirm", action=action)


def read_selectors(values: Iterable[str], use_stdin: bool) -> list[str]:
    selectors = list(values)
    if use_stdin:
        selectors.extend(line.strip() for line in sys.stdin.read().splitlines() if line.strip())
    return selectors


def parse_int(value: str, what: str) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        raise ValidationError(f"{what} must be an integer, got {value!r}", value=value) from None
