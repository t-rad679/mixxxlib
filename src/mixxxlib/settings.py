"""Parser for Mixxx's mixxx.cfg (spec §6.5).

The format is not INI: `[Section]` headers (whose names may contain brackets) followed by
`Key Value` lines split on the first space. Values may be empty or contain spaces.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from mixxxlib.errors import NotFoundError

Settings = dict[str, dict[str, str]]


def parse_mixxx_cfg(text: str) -> Settings:
    sections: Settings = {}
    current = ""
    for raw in text.splitlines():
        line = raw.strip()
        if not line:
            continue
        if line.startswith("[") and line.endswith("]"):
            current = line[1:-1]
            sections.setdefault(current, {})
            continue
        key, _, value = line.partition(" ")
        sections.setdefault(current, {})[key] = value.strip()
    return sections


def load_mixxx_cfg(path: Path) -> Settings:
    try:
        text = Path(path).read_text(encoding="utf-8", errors="replace")
    except FileNotFoundError:
        raise NotFoundError(f"mixxx.cfg not found at {path}", path=str(path)) from None
    return parse_mixxx_cfg(text)


def select(settings: Settings, selector: str | None) -> Any:
    if not selector:
        return settings
    section, _, key = selector.partition(".")
    if section not in settings:
        raise NotFoundError(f"section {section!r} not in mixxx.cfg", section=section)
    if not key:
        return settings[section]
    if key not in settings[section]:
        raise NotFoundError(f"key {key!r} not in section {section!r}", section=section, key=key)
    return settings[section][key]
