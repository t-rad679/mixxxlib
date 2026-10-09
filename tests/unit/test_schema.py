import sqlite3

import pytest

from mixxxlib.db import schema
from mixxxlib.errors import SchemaUnsupportedError


def _conn(version):
    conn = sqlite3.connect(":memory:")
    conn.execute("CREATE TABLE settings (name TEXT UNIQUE NOT NULL, value TEXT, locked INTEGER DEFAULT 0, hidden INTEGER DEFAULT 0)")
    if version is not None:
        conn.execute("INSERT INTO settings (name, value) VALUES ('mixxx.schema.version', ?)", (version,))
    return conn


def test_capabilities_are_cumulative():
    assert schema.capabilities_for(39) == frozenset()
    assert schema.capabilities_for(40) == {"tuning_frequency"}
    assert schema.capabilities_for(41) == {"tuning_frequency", "nested_crates"}
    assert schema.capabilities_for(42) == {"tuning_frequency", "nested_crates", "sibling_unique_rebuild"}
    assert schema.first_revision_with("nested_crates") == 41


def test_ceiling_and_source_are_pinned():
    assert schema.SCHEMA_CEILING == 40
    assert "94ccc34f" in schema.SCHEMA_SOURCE and schema.SCHEMA_SOURCE.endswith("res/schema.xml")


@pytest.mark.parametrize("version, verdict", [("39", "supported"), ("40", "unverified"), ("41", "unsupported"),
                                              ("38", "unsupported"), (None, "missing"), ("abc", "missing")])
def test_verdicts(version, verdict):
    info = schema.schema_info(_conn(version))
    assert info.verdict == verdict
    assert info.ceiling == 40
    assert info.raw == version


def test_require_schema_refuses_above_ceiling_and_names_it():
    with pytest.raises(SchemaUnsupportedError) as e:
        schema.require_schema(schema.schema_info(_conn("41")))
    assert e.value.details == {"version": 41, "ceiling": 40}
    assert "41" in e.value.message and "40" in e.value.message


def test_require_schema_warns_at_40():
    warnings = schema.require_schema(schema.schema_info(_conn("40")))
    assert [w.code for w in warnings] == ["schema_unverified"]


def test_require_schema_capability_names_needed_revision():
    with pytest.raises(SchemaUnsupportedError) as e:
        schema.require_schema(schema.schema_info(_conn("39")), capability="nested_crates")
    assert e.value.details == {"version": 39, "ceiling": 40, "capability": "nested_crates", "needs": 41}


def test_ceiling_override_admits_42():
    info = schema.schema_info(_conn("42"), ceiling=42)
    assert info.verdict == "unverified"
    assert "nested_crates" in info.capabilities
    assert schema.require_schema(info, capability="nested_crates")[0].code == "schema_unverified"


def test_table_columns_cover_the_tables_we_touch():
    assert set(schema.TABLE_COLUMNS) == {"library", "track_locations", "crates", "crate_tracks", "cues",
                                         "settings", "Playlists", "PlaylistTracks", "track_analysis"}
    assert "source_synchronized_ms" in schema.TABLE_COLUMNS["library"]
    assert schema.TABLE_COLUMNS["settings"] == ("name", "value", "locked", "hidden")
