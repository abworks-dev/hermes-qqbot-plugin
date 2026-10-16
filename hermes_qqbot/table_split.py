"""QQ-only Markdown table pagination; the base splitter still owns prose/fences."""
import re

from gateway.platforms.base import BasePlatformAdapter


def _cells(line: str) -> list[str]:
    # Pipes inside escaped text or inline code are data, not column boundaries.
    text = line.strip()
    cells = [""]
    code = ""
    for match in re.finditer(r"\\.|`+|\||[^\\`|]+|.", text):
        token = match[0]
        if token.startswith("`"):
            if not code:
                code = token
            elif token == code:
                code = ""
        if token == "|" and not code:
            cells.append("")
        else:
            cells[-1] += token
    if text.startswith("|"):
        cells = cells[1:]
    if text.endswith("|") and cells and not cells[-1]:
        cells = cells[:-1]
    return [cell.strip() for cell in cells]


def _table_header(lines: list[str], index: int) -> bool:
    if (index + 1 >= len(lines) or "|" not in lines[index]
            or lines[index].startswith(("    ", "\t"))):
        return False
    cells = _cells(lines[index + 1])
    return (len(cells) == len(_cells(lines[index])) and len(cells) > 1
            and all(re.fullmatch(r":?-{3,}:?", cell) for cell in cells))


def _blocks(content: str):
    """Yield (header, body); a None header is untouched non-table Markdown."""
    lines = content.splitlines()
    prose = []
    index = 0
    fence = None
    while index < len(lines):
        line = lines[index]
        marker = re.match(r"^\s{0,3}(`{3,}|~{3,})", line)
        if marker:
            token = marker[1]
            if fence is None:
                fence = token
            elif token[0] == fence[0] and len(token) >= len(fence):
                fence = None
            prose.append(line)
            index += 1
        elif fence is None and _table_header(lines, index):
            if prose:
                yield None, prose
                prose = []
            header = lines[index:index + 2]
            index += 2
            rows = []
            while index < len(lines) and "|" in lines[index] and lines[index].strip():
                if _table_header(lines, index):
                    break
                rows.append(lines[index])
                index += 1
            yield header, rows
        else:
            prose.append(line)
            index += 1
    if prose:
        yield None, prose


def _plain_parts(text: str, budget: int) -> list[str]:
    parts = BasePlatformAdapter.truncate_message(text, budget)
    if len(parts) > 1:
        # Only remove numbering produced by that splitter, not the source text.
        return [part.removesuffix(f" ({i + 1}/{len(parts)})") for i, part in enumerate(parts)]
    return parts


def _field_parts(text: str, budget: int) -> list[str]:
    """Keep individually sendable links whole in an oversized-row fallback."""
    parts = []
    position = 0
    pattern = r"\[[^\]\n]*\]\((?:[^()\n]|\([^()\n]*\))*\)|https?://\S+"
    for match in re.finditer(pattern, text):
        if match.start() > position:
            parts.extend(_plain_parts(text[position:match.start()], budget))
        link = match[0]
        if len(link) > budget:
            parts.extend(_plain_parts("链接本身超过单条长度，以下分段保留原文：\n" + link, budget))
        else:
            parts.append(link)
        position = match.end()
    if position < len(text):
        parts.extend(_plain_parts(text[position:], budget))
    return parts


def _table_parts(header: list[str], rows: list[str], budget: int) -> list[str]:
    title = "\n".join(header)
    parts = []
    current = title
    for row in rows:
        if len(title) + len(row) + 1 > budget:
            if current != title:
                parts.append(current)
                current = title
            # A single row cannot be made into a bounded table without losing data.
            names = _cells(header[0])
            fields = [f"{names[i] if i < len(names) else f'附加字段{i + 1}'}：{value}"
                      for i, value in enumerate(_cells(row))]
            parts.extend(_field_parts("表格条目（超长行，续段保留原顺序）\n" + "\n".join(fields), budget))
        else:
            if len(current) + len(row) + 1 > budget:
                parts.append(current)
                current = title
            current += "\n" + row
    if current != title or not rows:
        parts.append(current)
    return parts


def split_qq_tables(content: str, max_length: int = 4000) -> list[str]:
    """Repeat table headers at row boundaries, with bounded whole-message paging."""
    if len(content) <= max_length:
        return [content]
    blocks = list(_blocks(content))
    if not any(header for header, _ in blocks):
        return BasePlatformAdapter.truncate_message(content, max_length)
    # Number of output pages cannot exceed input characters for practical QQ limits.
    reserve = len(f"消息（{len(content)}/{len(content)}）\n\n")
    budget = max_length - reserve
    if budget < 64:
        return BasePlatformAdapter.truncate_message(content, max_length)
    units = []
    for header, body in blocks:
        text = "\n".join(body).strip("\n")
        if header:
            if len("\n".join(header)) + 1 > budget:
                # Preserve the oversized header once, then map rows by column number.
                units.extend(_field_parts("表格（超长表头，后续按列编号对应）\n" + "\n".join(header), budget))
                for row in body:
                    fields = [f"第{i + 1}列：{value}" for i, value in enumerate(_cells(row))]
                    text = "表格条目\n" + "\n".join(fields)
                    units.extend([text] if len(text) <= budget else _field_parts(text, budget))
            else:
                units.extend(_table_parts(header, body, budget))
        elif text:
            units.extend(_plain_parts(text, budget))
    pages = []
    current = ""
    for unit in units:
        candidate = current + "\n\n" + unit if current else unit
        if current and len(candidate) > budget:
            pages.append(current)
            current = unit
        else:
            current = candidate
    if current:
        pages.append(current)
    return [f"消息（{i + 1}/{len(pages)}）\n\n{page}" for i, page in enumerate(pages)]

