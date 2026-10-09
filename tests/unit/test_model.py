import pytest

from mixxxlib.errors import ValidationError
from mixxxlib.model import (
    CUE_TYPES, CUE_TYPE_IDS, WRITABLE_CUE_TYPES, MusicalKey, format_color, parse_color,
)


@pytest.mark.parametrize(
    "text, key_id",
    [
        ("C", 1), ("Db", 2), ("C#", 2), ("D", 3), ("Eb", 4), ("E", 5), ("F", 6), ("Gb", 7), ("F#", 7),
        ("G", 8), ("Ab", 9), ("A", 10), ("Bb", 11), ("B", 12),
        ("Cm", 13), ("C#m", 14), ("Dbm", 14), ("Dm", 15), ("Ebm", 16), ("Em", 17), ("Fm", 18),
        ("F#m", 19), ("Gbm", 19), ("Gm", 20), ("G#m", 21), ("Abm", 21), ("Am", 22), ("Bbm", 23), ("Bm", 24),
        ("E minor", 17), ("E min", 17), ("Emin", 17), ("E major", 5), ("Emaj", 5), ("e♭m", 16), ("A♯", 11),
    ],
)
def test_musical_key_parses_traditional_notation_keeping_the_text(text, key_id):
    key = MusicalKey.parse(text)
    assert key.id == key_id
    assert key.text == text.strip()


@pytest.mark.parametrize("value", [0, 17, "17", 24])
def test_musical_key_parses_bare_ids_with_canonical_text(value):
    key = MusicalKey.parse(value)
    assert key.id == int(value)
    assert key.text == {0: "", 17: "Em", 24: "Bm"}[int(value)]


@pytest.mark.parametrize("value", ["H", "Em7", 25, -1, "X minor", "C##"])
def test_musical_key_rejects_garbage(value):
    with pytest.raises(ValidationError):
        MusicalKey.parse(value)


def test_musical_key_none_and_empty_mean_no_key():
    assert MusicalKey.parse(None) == MusicalKey(0, "")
    assert MusicalKey.parse("") == MusicalKey(0, "")
    assert MusicalKey.from_db(None, None) == MusicalKey(0, "")
    assert MusicalKey.from_db("Em", 17) == MusicalKey(17, "Em")


@pytest.mark.parametrize(
    "value, expected",
    [("#C50A08", 0xC50A08), ("0xC50A08", 0xC50A08), ("12913160", 0xC50A08), (12913160, 0xC50A08),
     ("#FFC50A08", 0xC50A08), ("ffc50a08", None), (0xFFC50A08, 0xC50A08), ("0xFF8000", 0xFF8000)],
)
def test_parse_color_accepts_documented_forms_and_strips_alpha(value, expected):
    if expected is None:
        with pytest.raises(ValidationError):
            parse_color(value)
    else:
        assert parse_color(value) == expected


@pytest.mark.parametrize("value", ["#000000", 0, "0", 0xFF000000, -1, "#12345", "blue", True])
def test_parse_color_rejects_zero_negative_and_malformed(value):
    with pytest.raises(ValidationError):
        parse_color(value)


def test_parse_color_allow_zero_for_track_colour():
    assert parse_color("#000000", allow_zero=True) == 0


def test_format_color():
    assert format_color(0xC50A08) == "#C50A08"
    assert format_color(0) == "#000000"
    assert format_color(None) is None


def test_cue_type_tables():
    assert CUE_TYPES == {0: "invalid", 1: "hotcue", 2: "main", 3: "beat", 4: "loop", 5: "jump",
                         6: "intro", 7: "outro", 8: "n60db"}
    assert CUE_TYPE_IDS["hotcue"] == 1
    assert WRITABLE_CUE_TYPES == frozenset({"hotcue", "loop", "jump", "intro", "outro"})
