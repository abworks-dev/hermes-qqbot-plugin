"""Regression imports must resolve to this standalone plugin, not the builtin adapter."""

import importlib
from pathlib import Path

import pytest


@pytest.mark.parametrize("name", [
    "adapter", "api_errors", "clarify", "keyboards", "manage", "management",
    "message_rules", "references", "streaming", "table_split",
])
def test_behavior_modules_are_loaded_from_standalone_plugin(name):
    module = importlib.import_module(f"hermes_qqbot.{name}")
    package_root = Path(__file__).resolve().parents[1] / "hermes_qqbot"
    assert module.__name__ == f"hermes_qqbot.{name}"
    assert Path(module.__file__).resolve().parent == package_root.resolve()


def test_public_adapter_is_the_plugin_implementation():
    from hermes_qqbot import QQAdapter
    from hermes_qqbot.adapter import QQAdapter as implementation

    assert QQAdapter is implementation
    assert QQAdapter.__module__ == "hermes_qqbot.adapter"
    assert QQAdapter.send.__module__ == "hermes_qqbot.adapter"
