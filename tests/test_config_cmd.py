"""The `stumps config` helper: TOML round-trip + non-interactive setting."""

import argparse
import tomllib

import pytest

from stumps import config as cfg
from stumps.cli import _run_config


@pytest.fixture
def cfg_home(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    return tmp_path / "stumps" / "config.toml"


def test_dump_toml_roundtrips():
    data = {"team": ["India", "Mumbai Indians"], "region": "in",
            "domestic": "india", "cricketdata_api_key": 'a"b\\c'}
    assert tomllib.loads(cfg.dump_toml(data)) == data


def test_dump_toml_skips_none():
    assert "domestic" not in cfg.dump_toml({"region": "gb", "domestic": None})


def test_save_and_load_round_trip(cfg_home):
    path = cfg.save_config_file({"team": "Australia", "region": "au"})
    assert path == cfg_home and path.exists()
    assert oct(path.stat().st_mode)[-3:] == "600"  # private (may hold a key)
    assert cfg.load_config_file() == {"team": "Australia", "region": "au"}


def test_config_command_non_interactive_sets_keys(cfg_home, capsys):
    args = argparse.Namespace(
        show=False, team=["India"], region="in", domestic="sa",
        cricketdata_api_key="secret123",
    )
    rc = _run_config(args)
    assert rc == 0
    saved = cfg.load_config_file()
    assert saved["team"] == "India"  # single team stored as a string
    assert saved["region"] == "in"
    assert saved["domestic"] == "south-africa"  # alias resolved
    assert saved["cricketdata_api_key"] == "secret123"


def test_config_command_merges_with_existing(cfg_home):
    cfg.save_config_file({"team": "England", "cricketdata_api_key": "keepme"})
    args = argparse.Namespace(show=False, team=None, region="gb",
                              domestic=None, cricketdata_api_key=None)
    _run_config(args)
    saved = cfg.load_config_file()
    assert saved["region"] == "gb"
    assert saved["team"] == "England"  # untouched
    assert saved["cricketdata_api_key"] == "keepme"  # preserved


def test_config_show_does_not_write(cfg_home, capsys):
    args = argparse.Namespace(show=True, team=None, region=None, domestic=None,
                              cricketdata_api_key=None)
    assert _run_config(args) == 0
    assert not cfg_home.exists()  # --show is read-only
