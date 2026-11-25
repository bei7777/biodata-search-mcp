from collections.abc import Awaitable, Callable


async def cursor_window(
    fetch: Callable[[int, str | None], Awaitable[tuple[int, list[dict], str | None]]],
    limit: int, offset: int, max_page_size: int,
) -> tuple[int, list[dict]]:
    # 上游仅接受游标。按其允许的页大小跳过前面的记录，再截取所需区间。
    size = min(max_page_size, offset + limit)
    cursor = None
    position = 0
    selected: list[dict] = []
    while True:
        total, rows, next_cursor = await fetch(size, cursor)
        if offset >= total:
            return total, []
        start = max(0, offset - position)
        selected.extend(rows[start:start + limit - len(selected)])
        position += len(rows)
        if len(selected) == limit or position >= total:
            return total, selected
        if not rows or not next_cursor or next_cursor == cursor:
            raise RuntimeError("上游未提供下一页游标，无法完整读取指定区间")
        cursor = next_cursor
