from pathlib import Path

import pytest

from mixxxlib.errors import NotFoundError
from mixxxlib.settings import load_mixxx_cfg, parse_mixxx_cfg, select

FIXTURE = Path(__file__).resolve().parents[1] / "fixtures" / "mixxx.cfg"


def test_parses_space_separated_pairs_and_bracketed_section_names():
    cfg = load_mixxx_cfg(FIXTURE)
    assert cfg["Library"]["SyncTrackMetadataExport"] == "0"
    assert cfg["Config"]["Path"] == "/usr/share/mixxx/"
    assert cfg["EqualizerRack1_[Channel1]"]["EffectForGroup_[Channel1]"] == "org.mixxx.effects.bessel4lvmixeq"
    assert cfg["Controller"]["ControllerName"] == ""


def test_value_may_contain_spaces():
    cfg = parse_mixxx_cfg("[Channel1]\nvinylcontrol_pitch_estimator_type Kalman filter (accurate)\n")
    assert cfg["Channel1"]["vinylcontrol_pitch_estimator_type"] == "Kalman filter (accurate)"


def test_select_whole_section_and_single_key():
    cfg = load_mixxx_cfg(FIXTURE)
    assert select(cfg, None) == cfg
    assert select(cfg, "Library") == cfg["Library"]
    assert select(cfg, "Library.RescanOnStartup") == "1"


def test_select_missing_raises_not_found():
    cfg = load_mixxx_cfg(FIXTURE)
    with pytest.raises(NotFoundError) as e:
        select(cfg, "Nope")
    assert e.value.details == {"section": "Nope"}
    with pytest.raises(NotFoundError) as e:
        select(cfg, "Library.Nope")
    assert e.value.details == {"section": "Library", "key": "Nope"}


def test_missing_file_raises_not_found(tmp_path):
    with pytest.raises(NotFoundError) as e:
        load_mixxx_cfg(tmp_path / "mixxx.cfg")
    assert e.value.details["path"].endswith("mixxx.cfg")
