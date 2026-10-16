"""Isolated external-plugin tests, optionally against a frozen host gateway."""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

PLUGIN_ROOT = Path(__file__).resolve().parents[1]
HOST_ROOT = PLUGIN_ROOT.parent / "hermes-agent"
sys.path.insert(0, str(HOST_ROOT))
sys.path.insert(0, str(PLUGIN_ROOT))


def pytest_addoption(parser):
    parser.addoption("--qq-host-source", default=None,
                     help="Frozen host tree containing gateway/; no production edits")


def pytest_configure(config):
    frozen = config.getoption("--qq-host-source")
    if frozen:
        path = Path(frozen).resolve()
        if not (path / "gateway" / "stream_consumer.py").is_file():
            raise pytest.UsageError("--qq-host-source must contain gateway/stream_consumer.py")
        sys.path.insert(0, str(path))


@pytest.fixture(autouse=True)
def isolated_profile(tmp_path, monkeypatch):
    """Never inherit gateway credentials or home state from the invoking user."""
    home = tmp_path / "hermes-home"
    home.mkdir()
    monkeypatch.setenv("HERMES_HOME", str(home))
    monkeypatch.setenv("HERMES_RUNTIME_DIR", str(tmp_path / "runtime"))
    monkeypatch.setenv("HERMES_DISABLE_PLUGIN_DISCOVERY", "1")
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    return home
