"""Offline management previews and explicit dispatch contracts."""

import json
from unittest.mock import AsyncMock

import pytest

from hermes_qqbot import manage
from hermes_qqbot.management import QQBotManagementClient


@pytest.mark.parametrize("command,method,path", [
    (["menu-put"], "PUT", "/v2/menu"),
    (["panel-create"], "POST", "/v2/panels"),
    (["panel-update", "a/b"], "PUT", "/v2/panels/a%2Fb"),
    (["panel-targets", "id"], "PUT", "/v2/panels/id/target"),
    (["panel-delete", "id"], "DELETE", "/v2/panels/id"),
])
def test_write_preview_never_initializes(monkeypatch, tmp_path, capsys, command, method, path):
    payload = {"panel": {"remark": "中文", "items": []}}
    source = tmp_path / "input.json"
    source.write_text(json.dumps(payload), encoding="utf-8")
    if command[0] != "panel-delete":
        command += ["--file", str(source)]
    def forbidden(*args, **kwargs):
        raise AssertionError("execution must not start for previews")
    monkeypatch.setattr(manage, "execute_plan", forbidden)
    assert manage.main(command) == 0
    preview = json.loads(capsys.readouterr().out)
    assert (preview["method"], preview["path"]) == (method, path)
    assert preview["payload"] == (None if method == "DELETE" else payload)


@pytest.mark.parametrize("command,method,path", [
    (["menu-put"], "PUT", "/v2/menu"),
    (["panel-create"], "POST", "/v2/panels"),
    (["panel-update", "id"], "PUT", "/v2/panels/id"),
    (["panel-targets", "id"], "PUT", "/v2/panels/id/target"),
    (["panel-delete", "id"], "DELETE", "/v2/panels/id"),
])
def test_apply_forwards_exact_body(tmp_path, capsys, command, method, path):
    payload = {"menu": {"items": []}, "future_field": "untouched"}
    source = tmp_path / "input.json"
    source.write_text(json.dumps(payload), encoding="utf-8")
    if method != "DELETE":
        command += ["--file", str(source)]
    request = AsyncMock(return_value={"ok": True})
    assert manage.main(command + ["--apply"], client=QQBotManagementClient(request)) == 0
    request.assert_awaited_once_with(method, path, *(() if method == "DELETE" else (payload,)))
    assert json.loads(capsys.readouterr().out) == {"ok": True}


@pytest.mark.parametrize("command,path", [
    (["menu-get"], "/v2/menu"),
    (["panel-list", "--scope", "group"], "/v2/panels?scope=group"),
    (["panel-get", "a/b"], "/v2/panels/a%2Fb"),
])
def test_reads_execute_without_apply(capsys, command, path):
    request = AsyncMock(return_value={"items": []})
    assert manage.main(command, client=QQBotManagementClient(request)) == 0
    request.assert_awaited_once_with("GET", path)


@pytest.mark.parametrize("content", ["[]", "null", "not json", '{"secret":'])
def test_invalid_file_is_safe_error(tmp_path, capsys, content):
    source = tmp_path / "bad.json"
    source.write_text(content, encoding="utf-8")
    request = AsyncMock()
    assert manage.main(["menu-put", "--file", str(source), "--apply"],
                       client=QQBotManagementClient(request)) == 2
    request.assert_not_awaited()
    assert "JSON object" in capsys.readouterr().err


def test_non_utf8_input_is_rejected(tmp_path, capsys):
    source = tmp_path / "non-utf8.json"
    source.write_bytes(b'\xff\xfe')
    assert manage.main(["menu-put", "--file", str(source)]) == 2
    assert "UTF-8 JSON object" in capsys.readouterr().err


def test_request_failure_does_not_echo_secret(capsys):
    request = AsyncMock(side_effect=RuntimeError("token=TOP_SECRET raw response"))
    assert manage.main(["menu-get"], client=QQBotManagementClient(request)) == 1
    output = capsys.readouterr()
    assert "request failed" in output.err
    assert "TOP_SECRET" not in output.err + output.out


def test_real_execution_loads_current_home_and_rest_only(tmp_path, monkeypatch):
    import asyncio
    from types import SimpleNamespace
    from unittest.mock import Mock
    import dotenv
    import httpx
    import hermes_constants
    import gateway.config as config_module
    import hermes_qqbot.adapter as adapter_module

    config = config_module.PlatformConfig(extra={"app_id": "app", "client_secret": "secret"})
    monkeypatch.setattr(hermes_constants, "get_hermes_home", lambda: tmp_path)
    dotenv_loader = Mock()
    monkeypatch.setattr(dotenv, "load_dotenv", dotenv_loader)
    monkeypatch.setattr(config_module, "load_gateway_config", lambda: SimpleNamespace(
        platforms={config_module.Platform.QQBOT: config}))
    request = AsyncMock(return_value={"menu": {}})
    adapter = SimpleNamespace(_app_id="app", _client_secret="secret", _http_client=None,
                              management=QQBotManagementClient(request))
    adapter_factory = Mock(return_value=adapter)
    monkeypatch.setattr(adapter_module, "QQAdapter", adapter_factory)
    http = object()

    class RestContext:
        async def __aenter__(self):
            return http

        async def __aexit__(self, *args):
            assert adapter._http_client is None

    factory = Mock(return_value=RestContext())
    monkeypatch.setattr(httpx, "AsyncClient", factory)
    plan = manage.build_plan(manage.parse_args(["menu-get"]))
    assert asyncio.run(manage.execute_plan(plan)) == {"menu": {}}
    # Scoped credentials belong to the canonical reader, not process-global env.
    dotenv_loader.assert_not_called()
    adapter_factory.assert_called_once_with(config)
    factory.assert_called_once_with(timeout=30, follow_redirects=False)
    request.assert_awaited_once_with("GET", "/v2/menu")


def test_missing_file_is_nonzero(tmp_path, capsys):
    assert manage.main(["menu-put", "--file", str(tmp_path / "absent.json")]) == 2
    assert "readable UTF-8 JSON object" in capsys.readouterr().err
