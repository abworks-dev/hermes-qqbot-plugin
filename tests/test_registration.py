"""Behavior contracts for optional platform/CLI/tool integration."""
from types import SimpleNamespace
from unittest.mock import Mock
import json

from hermes_qqbot import plugin, tool_override


class Context:
    plugin_id = "qqbot-standalone"

    def __init__(self, allowed=False):
        self.allowed = allowed
        self.platform = None
        self.cli = None
        self.tool = None

    def has_capability(self, name):
        return self.allowed and name == "tools.override"

    def register_platform(self, **kwargs):
        self.platform = kwargs

    def register_cli_command(self, *args):
        self.cli = args

    def register_tool(self, **kwargs):
        self.tool = kwargs
        return kwargs


def test_real_discovery_loads_platform_without_tool_consent(isolated_profile, monkeypatch, tmp_path):
    import shutil
    from pathlib import Path
    from hermes_cli import plugins as host
    from hermes_cli import plugins_discovery
    from gateway.platform_registry import platform_registry
    source = Path(__file__).resolve().parents[1]
    installed = isolated_profile / "plugins" / "qqbot-standalone"
    shutil.copytree(source, installed, ignore=shutil.ignore_patterns(".git", "__pycache__", ".pytest_cache", "tests"))
    (isolated_profile / "config.yaml").write_text(
        "plugins:\n  enabled: [qqbot-standalone]\n  isolation: in_process\n", encoding="utf-8")
    monkeypatch.delenv("HERMES_DISABLE_PLUGIN_DISCOVERY", raising=False)
    monkeypatch.setattr(host, "get_bundled_plugins_dir", lambda: tmp_path / "no-bundled-plugins")
    monkeypatch.setattr(plugins_discovery, "discover_entrypoint_manifests", lambda: [])
    manager = host.PluginManager(scope_key=str(isolated_profile))
    try:
        manager.discover_and_load()
        entry = platform_registry.get("qqbot")
        assert entry is not None
        assert entry.source == "plugin"
        assert entry.allowed_users_env == "QQ_ALLOWED_USERS"
        assert entry.standalone_sender_fn.__module__.endswith("hermes_qqbot.senders")
        assert manager._cli_commands["qqbot"]["plugin"] == "qqbot-standalone"
        assert not manager._plugin_tool_names
    finally:
        manager.unload()


def test_default_registration_loads_adapter_and_cli_without_tool_consent(caplog):
    ctx = Context()
    plugin.register(ctx)
    assert ctx.platform["name"] == "qqbot"
    assert ctx.platform["allowed_users_env"] == "QQ_ALLOWED_USERS"
    assert ctx.platform["allow_all_env"] == "QQ_ALLOW_ALL_USERS"
    assert ctx.platform["cron_deliver_env_var"] == "QQBOT_HOME_CHANNEL"
    assert ctx.platform["required_env"] == ["QQ_APP_ID", "QQ_CLIENT_SECRET"]
    assert ctx.platform["setup_fn"] is plugin.setup_qqbot
    assert callable(ctx.platform["standalone_sender_fn"])
    assert ctx.cli[0] == "qqbot"
    assert ctx.tool is None
    assert "explicitly grant" in caplog.text


def _entry(original):
    return SimpleNamespace(name="send_message", toolset="messaging", schema={"name": "send_message"},
                           handler=original, check_fn=Mock(return_value=False), requires_env=["EXAMPLE"],
                           is_async=False, description="Original", emoji="X", dynamic_schema_overrides=None,
                           execution_timeout=None, max_result_size_chars=None)


def test_override_preserves_security_gate_and_delegates_nonqq(monkeypatch):
    from tools.registry import registry
    original = Mock(return_value="original")
    entry = _entry(original)
    monkeypatch.setattr(registry, "get_entry", lambda name: entry)
    own_send = Mock(return_value="plugin")
    monkeypatch.setattr(tool_override, "_handle_send", own_send)
    ctx = Context(allowed=True)
    tool_override.register_override(ctx)
    assert ctx.tool["override"] is True
    assert ctx.tool["check_fn"] is entry.check_fn
    assert ctx.tool["schema"] is entry.schema
    assert ctx.tool["requires_env"] is entry.requires_env
    handler = ctx.tool["handler"]
    assert handler({"target": "telegram:123", "message": "hi"}, task_id="t") == "original"
    original.assert_called_once_with({"target": "telegram:123", "message": "hi"}, task_id="t")
    assert handler({"target": "qqbot:123", "action": "react"}) == "original"
    assert handler({"target": " QQBOT :123", "message": "hi"}) == "plugin"
    own_send.assert_called_once()


def test_absent_tool_is_not_enabled_even_with_override_consent(monkeypatch, caplog):
    from tools.registry import registry
    monkeypatch.setattr(registry, "get_entry", lambda name: None)
    ctx = Context(allowed=True)
    tool_override.register_override(ctx)
    assert ctx.tool is None
    assert "will not enable a tool disabled by host policy" in caplog.text


def test_qq_validation_refuses_unauthorized_relay_before_transport(monkeypatch):
    from tools import send_message_tool as core
    import gateway.config
    import tools.interrupt
    monkeypatch.setattr(core, "_resolve_tool_target", lambda target: ("qqbot", "123", None, None))
    monkeypatch.setattr(tools.interrupt, "is_interrupted", lambda: False)
    monkeypatch.setattr(gateway.config, "load_gateway_config", lambda: SimpleNamespace())
    monkeypatch.setattr(core, "_resolve_platform_config", lambda *a: (
        "qqbot", SimpleNamespace(token="owned"), None, None))
    monkeypatch.setattr(core, "_maybe_skip_cron_duplicate_send", lambda *a: None)
    monkeypatch.setattr(core, "_authorize_relay_target", lambda *a, **kw: "egress denied")
    send = Mock(side_effect=AssertionError("must not send"))
    monkeypatch.setattr(tool_override, "_send_to_platform", send)
    result = json.loads(tool_override._handle_send({"target": "qqbot:123", "message": "hello"}))
    assert "egress denied" in result["error"]
    send.assert_not_called()


def test_cli_write_preview_does_not_open_client(tmp_path, capsys):
    import argparse
    from hermes_qqbot.manage import run_args
    parser = argparse.ArgumentParser()
    plugin._cli_setup(parser)
    payload = tmp_path / "menu.json"
    payload.write_text('{"menu":{"items":[]}}', encoding="utf-8")
    args = parser.parse_args(["menu-put", "--file", str(payload)])
    client = Mock()
    assert run_args(args, client=client) == 0
    assert json.loads(capsys.readouterr().out)["dry_run"] is True
    assert client.mock_calls == []


def test_cli_send_preview_does_not_resolve_credentials(monkeypatch, capsys):
    from hermes_qqbot.manage import main
    send = Mock(side_effect=AssertionError("preview must stay offline"))
    monkeypatch.setattr(tool_override, "_handle_send", send)
    assert main(["send", "qqbot:123", "hello"]) == 0
    assert json.loads(capsys.readouterr().out)["dry_run"] is True
    send.assert_not_called()


def test_cli_send_apply_uses_validated_handler(monkeypatch, capsys):
    from hermes_qqbot.manage import main
    send = Mock(return_value=json.dumps({"error": "egress denied"}))
    monkeypatch.setattr(tool_override, "_handle_send", send)
    assert main(["send", "qqbot:123", "hello", "--apply"]) == 1
    assert json.loads(capsys.readouterr().out)["error"] == "egress denied"
    send.assert_called_once_with({"action": "send", "target": "qqbot:123", "message": "hello"})
