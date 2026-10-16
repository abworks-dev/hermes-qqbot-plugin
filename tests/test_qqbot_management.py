"""Wire contracts for explicit QQ menu/panel management (no network calls)."""

from unittest.mock import AsyncMock
from urllib.parse import parse_qs, urlsplit

import pytest

from hermes_qqbot.management import QQBotManagementClient


def test_construction_has_no_implicit_requests():
    request = AsyncMock()
    QQBotManagementClient(request)
    request.assert_not_called()


@pytest.mark.asyncio
async def test_reads_preserve_responses_and_never_publish():
    response = {"version": 4, "menu": None, "next_cursor": "more", "is_end": False}
    request = AsyncMock(return_value=response)
    client = QQBotManagementClient(request)
    assert await client.get_global_menu() is response
    request.assert_awaited_once_with("GET", "/v2/menu")
    request.reset_mock()
    assert await client.list_panels("c2c") is response
    request.assert_awaited_once_with("GET", "/v2/panels?scope=c2c")
    request.reset_mock()
    assert await client.get_panel("p_one") is response
    request.assert_awaited_once_with("GET", "/v2/panels/p_one")


@pytest.mark.asyncio
async def test_list_encodes_query_and_does_not_follow_cursor():
    request = AsyncMock(return_value={"next_cursor": "next", "is_end": False})
    client = QQBotManagementClient(request)
    await client.list_panels("group", cursor="a+/&?= 中文", limit=50)
    request.assert_awaited_once()
    method, path = request.await_args.args
    assert method == "GET"
    assert urlsplit(path).path == "/v2/panels"
    assert parse_qs(urlsplit(path).query) == {
        "scope": ["group"], "cursor": ["a+/&?= 中文"], "limit": ["50"],
    }
    request.reset_mock()
    await client.list_panels("dm", cursor="")
    request.assert_awaited_once_with("GET", "/v2/panels?scope=dm&cursor=")


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "operation,args,method,path,payload",
    [
        ("update_global_menu", (), "PUT", "/v2/menu", {"menu": {"items": [
            {"type": "switch", "switch": {"switch_id": "search", "default": False}},
            {"type": "menu", "sub_menu_items": [{"type": "send_message", "send_message": "/help"}]},
        ]}}),
        ("update_global_menu", (), "PUT", "/v2/menu", {}),
        ("create_panel", (), "POST", "/v2/panels", {
            "scope": "group", "target_type": "specific", "group_openids": ["g1"],
            "panel": {"items": [{"type": "command", "name": "/help", "only_admin": True}], "version": 2},
        }),
        ("create_panel", (), "POST", "/v2/panels", {"scope": "channel", "panel": {}}),
        ("update_panel", ("p1",), "PUT", "/v2/panels/p1", {"panel": {"items": [], "remark": "", "version": 3}}),
        ("update_panel_targets", ("p1",), "PUT", "/v2/panels/p1/target", {"op": "add", "group_openids": ["g1"]}),
        ("update_panel_targets", ("p1",), "PUT", "/v2/panels/p1/target", {"op": "del", "user_openids": ["u1"]}),
    ],
)
async def test_explicit_writes_forward_full_body(operation, args, method, path, payload):
    response = {"version": 9, "panel_id": "new_id"}
    request = AsyncMock(return_value=response)
    client = QQBotManagementClient(request)
    assert await getattr(client, operation)(*args, payload) is response
    request.assert_awaited_once_with(method, path, payload)
    assert request.await_args.args[2] is payload


@pytest.mark.asyncio
@pytest.mark.parametrize("operation", ["get_panel", "update_panel", "delete_panel", "update_panel_targets"])
async def test_panel_ids_are_single_encoded_path_segments(operation):
    request = AsyncMock(return_value={})
    client = QQBotManagementClient(request)
    args = ("p/with ?#%中文",)
    if operation in {"update_panel", "update_panel_targets"}:
        args += ({},)
    assert await getattr(client, operation)(*args) == {}
    path = request.await_args.args[1]
    expected = "/v2/panels/p%2Fwith%20%3F%23%25%E4%B8%AD%E6%96%87"
    assert path == expected + ("/target" if operation == "update_panel_targets" else "")
    assert request.await_args.args[0] == {
        "get_panel": "GET", "update_panel": "PUT", "delete_panel": "DELETE", "update_panel_targets": "PUT",
    }[operation]
    request.assert_awaited_once()


@pytest.mark.asyncio
async def test_api_errors_propagate_without_retry_or_success_fallback():
    error = RuntimeError("QQ Bot API error [400] /v2/menu: rejected")
    request = AsyncMock(side_effect=error)
    client = QQBotManagementClient(request)
    with pytest.raises(RuntimeError) as raised:
        await client.update_global_menu({"menu": {}})
    assert raised.value is error
    request.assert_awaited_once_with("PUT", "/v2/menu", {"menu": {}})
