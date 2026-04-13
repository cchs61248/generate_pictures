"""
共用 helper 函式，供所有 router 模組引用。
"""
import asyncio
import contextlib
import json
import os
import re

from fastapi import HTTPException
from fastapi.responses import StreamingResponse


def project_root() -> str:
    """回傳專案根目錄絕對路徑（api/ 的上層）。"""
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def safe_session_id(session_id: str | None) -> str | None:
    """驗證並回傳清理後的 session_id；空值回 None，非法值拋 400。"""
    if not session_id:
        return None
    sid = session_id.strip()
    if not sid:
        return None
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", sid):
        raise HTTPException(status_code=400, detail="invalid session_id")
    return sid


def readable_error(exc: Exception) -> str:
    """將例外轉為人類可讀的錯誤字串。"""
    detail = str(exc).strip()
    name = exc.__class__.__name__
    if detail:
        if detail == name:
            return detail
        return f"{name}: {detail}"
    return name


def sample_image_path_for_session(root: str, session_id: str | None) -> str:
    """回傳該 session 的參考圖絕對路徑；無 sid 時回退至根目錄 sample.jpg。"""
    sid = safe_session_id(session_id)
    if sid:
        uploads_dir = os.path.join(root, "uploads")
        os.makedirs(uploads_dir, exist_ok=True)
        return os.path.join(uploads_dir, f"{sid}.jpg")
    return os.path.join(root, "sample.jpg")


def upload_image_path_for_session(root: str, session_id: str) -> str:
    """回傳該 session 的上傳圖絕對路徑；sid 不合法時拋 400。"""
    sid = safe_session_id(session_id)
    if not sid:
        raise HTTPException(status_code=400, detail="invalid session_id")
    return os.path.join(root, "uploads", f"{sid}.jpg")


def require_session_upload_exists(session_id: str | None) -> None:
    """
    電商主流程前置條件：須帶合法 session_id，且 uploads/<session_id>.jpg 已存在。
    避免未上傳商品圖時仍誤用根目錄 sample.jpg 執行。
    """
    sid = safe_session_id(session_id)
    if not sid:
        raise HTTPException(
            status_code=400,
            detail="請先上傳一張商品圖（缺少或無效的 session_id）。",
        )
    path = os.path.join(project_root(), "uploads", f"{sid}.jpg")
    if not os.path.isfile(path):
        raise HTTPException(
            status_code=400,
            detail="請先上傳一張商品圖後再執行。",
        )


def apply_session_sample_path(config, session_id: str | None):
    """將 session 的圖片路徑與 final_output_path 套用至 config 物件。"""
    root = project_root()
    sid = safe_session_id(session_id)
    config.sample_image_path = sample_image_path_for_session(root, sid)
    config.session_id = sid or ""
    if sid:
        template_dir = os.path.join(root, "template_json")
        config.final_output_path = os.path.join(template_dir, f"final_output_{sid}.json")
    return config


def safe_filename_part(text: str, max_len: int = 40) -> str:
    """將任意字串轉成適合作為檔名的安全片段。"""
    safe = re.sub(r'[\\/:*?"<>|\s]+', "_", text.strip())
    return safe[:max_len] if safe else "thread"


async def sse_streaming_response(generator_func, request) -> StreamingResponse:
    """
    通用 SSE StreamingResponse 包裝器。

    generator_func 接受一個 asyncio.Queue，負責將事件 put 進去；
    queue 中的每個 item 須為 dict，完成時 type 為 "complete" 或 "error"。
    """
    async def event_stream():
        queue: asyncio.Queue = asyncio.Queue()
        task = asyncio.create_task(generator_func(queue))
        try:
            while True:
                if await request.is_disconnected():
                    task.cancel()
                    break
                try:
                    item = await asyncio.wait_for(queue.get(), timeout=0.25)
                except asyncio.TimeoutError:
                    continue
                yield f"data: {json.dumps(item, ensure_ascii=False)}\n\n"
                if item.get("type") in ("complete", "error"):
                    break
        finally:
            if not task.done():
                task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )
