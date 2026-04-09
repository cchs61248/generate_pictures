"""Pipeline 進度：供 CLI 以外的串流介面（如 SSE）回報階段與工具日誌。"""

from __future__ import annotations

import asyncio
import contextvars
from typing import Any


class ProgressBus:
    """將事件放入 asyncio.Queue；同步工具執行緒用 emit_sync。"""

    def __init__(self, queue: asyncio.Queue, loop: asyncio.AbstractEventLoop) -> None:
        self._queue = queue
        self._loop = loop

    async def emit(self, payload: dict[str, Any]) -> None:
        await self._queue.put(payload)

    def emit_sync(self, payload: dict[str, Any]) -> None:
        asyncio.run_coroutine_threadsafe(self._queue.put(payload), self._loop)


progress_cv: contextvars.ContextVar[ProgressBus | None] = contextvars.ContextVar(
    "pipeline_progress", default=None
)


def get_progress_bus() -> ProgressBus | None:
    return progress_cv.get()


# 折疊區 group_id（與階段內 emit 一致）
GROUP_STAGE1_TOOLS = "stage1_tools"
GROUP_STAGE2_META = "stage2_meta"
GROUP_STAGE3_META = "stage3_meta"
