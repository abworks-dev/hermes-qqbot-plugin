"""Behavior contracts for QQ's official inline keyboard wire model."""

import pytest

from hermes_qqbot.keyboards import (
    InlineKeyboard,
    KeyboardButton,
    KeyboardButtonAction,
    KeyboardButtonModal,
    KeyboardButtonPermission,
    KeyboardButtonRenderData,
    KeyboardContent,
    KeyboardRow,
    build_approval_keyboard,
    build_update_prompt_keyboard,
    parse_approval_button_data,
    parse_update_prompt_button_data,
)


@pytest.mark.parametrize("action_type,data", [
    (0, "https://example.com"), (0, "mqqapi://miniapp/open"),
    (1, "callback:opaque"), (2, "/help"),
])
def test_action_wire_types_and_optional_defaults(action_type, data):
    action = KeyboardButtonAction(action_type, data)
    assert action.to_dict() == {
        "type": action_type, "data": data, "permission": {"type": 2},
    }


def test_full_command_preserves_explicit_falsy_values():
    action = KeyboardButtonAction(
        2, "", KeyboardButtonPermission(0, [], ["role"]), 0,
        unsupport_tips="", enter=False, reply=False, anchor=0,
        modal=KeyboardButtonModal("确认执行？", "确认", "取消"),
    )
    wire = action.to_dict()
    assert wire["permission"] == {
        "type": 0, "specify_user_ids": [], "specify_role_ids": ["role"],
    }
    assert wire["click_limit"] == 0
    assert wire["enter"] is False and wire["reply"] is False
    assert wire["anchor"] == 0 and wire["unsupport_tips"] == ""
    assert wire["modal"] == {"content": "确认执行？", "confirm_text": "确认", "cancel_text": "取消"}


@pytest.mark.parametrize("permission_type", [0, 1, 2])
def test_permissions_keep_user_and_role_identifiers(permission_type):
    permission = KeyboardButtonPermission(permission_type, ["openid"], ["roleid"])
    assert permission.to_dict()["specify_user_ids"] == ["openid"]
    assert permission.to_dict()["specify_role_ids"] == ["roleid"]


@pytest.mark.parametrize("style", [0, 1, 3, 4])
def test_rendering_supported_styles_and_optional_visited_label(style):
    render = KeyboardButtonRenderData("十" * 10, style=style)
    assert render.to_dict() == {"label": "十" * 10, "style": style}
    assert KeyboardButtonRenderData("", "", style).to_dict()["visited_label"] == ""


def test_template_and_inline_keyboard_shapes():
    assert InlineKeyboard(id="template").to_dict() == {"id": "template"}
    assert InlineKeyboard().to_dict() == {"content": {"rows": []}}
    button = KeyboardButton("link", KeyboardButtonRenderData("打开"), KeyboardButtonAction(0, "https://example.com"))
    wire = InlineKeyboard(KeyboardContent([KeyboardRow([button])])).to_dict()
    assert wire["content"]["rows"][0]["buttons"][0] == {
        "id": "link", "render_data": {"label": "打开", "style": 1},
        "action": {"type": 0, "data": "https://example.com", "permission": {"type": 2}},
    }
    with pytest.raises(ValueError, match="mutually exclusive"):
        InlineKeyboard(KeyboardContent(), id="template")


@pytest.mark.parametrize("factory", [
    lambda: KeyboardButtonAction(3, "bad"),
    lambda: KeyboardButtonPermission(3),
    lambda: KeyboardButtonPermission(0, "not-a-list"),
    lambda: KeyboardButtonPermission(0, specify_role_ids=[42]),
    lambda: KeyboardButtonRenderData("x" * 11),
    lambda: KeyboardButtonRenderData("ok", style=2),
    lambda: KeyboardButtonModal("x" * 41, "yes", "no"),
    lambda: KeyboardButtonModal("ok", "x" * 5, "no"),
    lambda: KeyboardButtonModal("ok", "yes", "x" * 5),
    lambda: KeyboardButtonModal("See https://example.com", "yes", "no"),
    lambda: KeyboardButtonModal("WWW.example.com", "yes", "no"),
    lambda: KeyboardButton("id", KeyboardButtonRenderData("ok"), KeyboardButtonAction(2, "/help"), "group"),
])
def test_unambiguous_invalid_contract_values_are_rejected(factory):
    with pytest.raises(ValueError):
        factory()


def test_modal_boundary_and_legacy_positional_constructors():
    assert KeyboardButtonModal("字" * 40, "字" * 4, "字" * 4).to_dict()["content"] == "字" * 40
    action = KeyboardButtonAction(1, "data", KeyboardButtonPermission(), 1)
    button = KeyboardButton("id", KeyboardButtonRenderData("label", "visited", 0), action, "default")
    assert button.to_dict()["action"]["click_limit"] == 1
    assert button.to_dict()["group_id"] == "default"


def test_approval_and_update_helpers_keep_routing_and_permanent_scope():
    session = "agent:main:qqbot:c2c:OPENID"
    buttons = build_approval_keyboard(session).to_dict()["content"]["rows"][0]["buttons"]
    assert [parse_approval_button_data(b["action"]["data"]) for b in buttons] == [
        (session, "allow-once"), (session, "allow-always"), (session, "deny"),
    ]
    assert all(b["group_id"] == "approval" and b["action"]["type"] == 1 for b in buttons)
    restricted = build_approval_keyboard(session, allow_permanent=False).to_dict()["content"]["rows"][0]["buttons"]
    assert [parse_approval_button_data(b["action"]["data"])[1] for b in restricted] == ["allow-once", "deny"]
    update = build_update_prompt_keyboard().to_dict()["content"]["rows"][0]["buttons"]
    assert [parse_update_prompt_button_data(b["action"]["data"]) for b in update] == ["y", "n"]
    assert parse_approval_button_data("approve:session:allow-always:extra") is None
    assert parse_update_prompt_button_data("update_prompt:yes") is None
