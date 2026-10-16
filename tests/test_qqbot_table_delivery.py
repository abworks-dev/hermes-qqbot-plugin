"""QQ table splitting at the real adapter-to-REST seam."""
from unittest.mock import AsyncMock

import pytest

from gateway.config import PlatformConfig
from hermes_qqbot import QQAdapter
from gateway.platforms.base import BasePlatformAdapter
from hermes_qqbot.table_split import split_qq_tables


def test_multiple_tables_escaped_pipes_and_fenced_examples():
    header = "| 价格 | 联系 |\n|:---|---:|"
    rows = [f"| {i}元 | [入口](https://example.com/{i}) 备注\\|保留 |" for i in range(40)]
    code = "```markdown\n| 示例 | 值 |\n|---|---|\n| A | B |\n```"
    content = code + "\n\n" + header + "\n" + "\n".join(rows) + "\n\n第二张表\n\n" + header + "\n| 99元 | end |\n\n来源保留"
    parts = split_qq_tables(content, 500)
    assert code in parts[0]
    assert all(len(p) <= 500 for p in parts)
    assert [line for p in parts for line in p.splitlines() if line in rows] == rows
    for p in parts:
        if any(row in p for row in rows) or "| 99元 | end |" in p:
            assert header in p
    assert "来源保留" in parts[-1]


@pytest.mark.parametrize("text", ["短消息", "正文\n" * 2000, "```md\n" + "示例\n" * 2000 + "```"])
def test_non_table_uses_original_splitter(text):
    assert split_qq_tables(text, 4000) == BasePlatformAdapter.truncate_message(text, 4000)


def test_single_oversized_row_keeps_all_fields_as_labeled_text():
    content = "| 商家 | 联系 |\n|---|---|\n| 唯一商家 | " + "甲" * 1300 + "尾部唯一 |"
    parts = split_qq_tables(content, 400)
    assert all(len(p) <= 400 for p in parts)
    joined = "\n".join(parts)
    assert "商家：唯一商家" in joined
    assert "联系：" in joined
    assert "尾部唯一" in joined
    assert joined.count("甲") == 1300


def test_indented_code_stays_code_in_mixed_table_message():
    code = "    print('x')\n    | not | table |\n    |---|---|\n    | code | row |"
    table = "| a | b |\n|---|---|\n" + "\n".join(f"| {i} | value |" for i in range(60))
    parts = split_qq_tables(code + "\n\n" + table, 500)
    assert code in parts[0]
    assert sum(p.count("    |---|---|") for p in parts) == 1


def test_ragged_rows_and_inline_code_pipes_remain_in_tables():
    header = "| `a|b` | c |\n|---|---|"
    rows = [f"| row{i} |" if i % 2 else f"| row{i} | value | extra |" for i in range(80)]
    parts = split_qq_tables(header + "\n" + "\n".join(rows), 500)
    assert all(header in p for p in parts)
    assert [line for p in parts for line in p.splitlines() if line in rows] == rows


def test_oversized_header_keeps_rows_and_links():
    link = "[入口](https://example.com/" + "x" * 80 + ")"
    row = f"| 商家唯一 | {link} |"
    content = "| " + "超长列名" * 160 + " | 联系 |\n|---|---|\n" + row
    parts = split_qq_tables(content, 400)
    assert all(len(p) <= 400 for p in parts)
    assert sum(link in p for p in parts) == 1
    assert any("第1列：商家唯一" in p and link in p for p in parts)
    assert sum(p.count("超") for p in parts) == 161  # 160 field-name repetitions plus fallback heading


def test_oversized_row_keeps_sendable_link_atomic():
    link = "[入口](https://example.com/" + "x" * 80 + ")"
    content = "| 商家 | 备注 |\n|---|---|\n| 唯一 | " + "甲" * 600 + link + "尾部 |"
    parts = split_qq_tables(content, 400)
    assert sum(link in p for p in parts) == 1
    assert "".join(parts).count("甲") == 600
    assert "尾部" in parts[-1]


@pytest.mark.asyncio
async def test_quota_overflow_is_explicit_and_does_not_replay(monkeypatch):
    adapter = QQAdapter(PlatformConfig(enabled=True, extra={
        "app_id": "test", "client_secret": "test", "markdown_support": True,
    }))
    monkeypatch.setattr(adapter, "_ensure_connected", AsyncMock(return_value=True))
    monkeypatch.setattr(adapter, "_guess_chat_type", lambda _: "group")
    post = AsyncMock(return_value={"id": "sent"})
    monkeypatch.setattr(adapter, "_api_request", post)
    content = "| 商家 | 联系 |\n|---|---|\n" + "\n".join(f"| 商家{i} | {'资料' * 200} |" for i in range(80))
    result = await adapter._send_with_retry("target", content, reply_to="incoming")
    assert not result.success
    assert post.call_count == 5
    assert "未发送完整" in post.call_args_list[-1].args[2]["markdown"]["content"]
    assert result.raw_response["undelivered_chunks"]



@pytest.mark.asyncio
async def test_long_table_remains_independent_tables_without_losing_rows(monkeypatch):
    adapter = QQAdapter(PlatformConfig(enabled=True, extra={
        "app_id": "test", "client_secret": "test", "markdown_support": True,
    }))
    monkeypatch.setattr(adapter, "_ensure_connected", AsyncMock(return_value=True))
    monkeypatch.setattr(adapter, "_guess_chat_type", lambda _: "group")
    bodies = []

    async def post(method, path, body):
        bodies.append(body)
        return {"id": str(len(bodies))}

    monkeypatch.setattr(adapter, "_api_request", post)
    header = "| 价格 | 商家 | 限制 | 联系 |\n|---|---|---|---|"
    rows = [f"| {i}元 | 商家{i:03} [入口](https://example.com/{i}) | {'待核实' * 15} | 无联系方式 |" for i in range(65)]
    content = "价格升序\n\n" + header + "\n" + "\n".join(rows) + "\n\n来源：资料A，第1–65行。"
    result = await adapter.send("target", content, reply_to="incoming")
    assert result.success
    payloads = [body["markdown"]["content"] for body in bodies]
    assert 1 < len(payloads) <= 5
    for payload in payloads:
        assert len(payload) <= adapter.MAX_MESSAGE_LENGTH
        if any(row in payload for row in rows):
            assert header in payload
    assert [row for payload in payloads for row in payload.splitlines() if row in rows] == rows
    assert "来源：资料A，第1–65行。" in payloads[-1]
    assert all(body["msg_id"] == "incoming" for body in bodies)
    assert len({body["msg_seq"] for body in bodies}) == len(bodies)
