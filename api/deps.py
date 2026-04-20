"""
共用 helper 函式，供所有 router 模組引用。
"""
import asyncio
import contextlib
import json
import os
import re
import uuid
from collections.abc import AsyncIterator
from typing import Any

from fastapi import HTTPException
from fastapi.responses import StreamingResponse

from core.app_logging import get_backend_logger
from core.config import resolve_project_root

logger = get_backend_logger("deps")


def new_request_id() -> str:
    """產生短 request id，供跨模組 log 串聯。"""
    return uuid.uuid4().hex[:12]


def log_extra(session_id: str | None = None, request_id: str | None = None) -> dict[str, str]:
    return {
        "sid": session_id or "-",
        "rid": request_id or "-",
    }


def project_root() -> str:
    """回傳專案根目錄絕對路徑（api/ 的上層）；與 parse_config、token 紀錄共用 APP_RUNTIME_ROOT 規則。"""
    return resolve_project_root()


def safe_session_id(session_id: str | None) -> str | None:
    """驗證並回傳清理後的 session_id；空值回 None，非法值拋 400。"""
    if not session_id:
        return None
    sid = session_id.strip()
    if not sid:
        return None
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", sid):
        logger.warning("[deps] invalid session_id format: %s", sid)
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
        logger.warning("[deps] missing/invalid session_id before run")
        raise HTTPException(
            status_code=400,
            detail="請先上傳一張商品圖（缺少或無效的 session_id）。",
        )
    path = os.path.join(project_root(), "uploads", f"{sid}.jpg")
    if not os.path.isfile(path):
        logger.warning("[deps] upload image missing | sid=%s path=%s", sid, path)
        raise HTTPException(
            status_code=400,
            detail="請先上傳一張商品圖後再執行。",
        )


def apply_session_sample_path(config, session_id: str | None):
    """將 session 的圖片路徑與 final_output_path 套用至 config 物件。"""
    root = project_root()
    sid = safe_session_id(session_id)
    config.sample_image_path = sample_image_path_for_session(root, sid)
    config.picture_dir = os.path.join(root, "picture")
    config.session_id = sid or ""
    if sid:
        template_dir = os.path.join(root, "template_json")
        config.final_output_path = os.path.join(template_dir, f"final_output_{sid}.json")
    logger.debug(
        "[deps] apply session paths | sid=%s sample=%s final_json=%s",
        sid or "(none)",
        config.sample_image_path,
        config.final_output_path,
    )
    return config


def doc_upload_path(root: str, sid: str, filename: str) -> str:
    """回傳文件檔案的儲存路徑：uploads/<sid>_doc_<filename>。"""
    uploads_dir = os.path.join(root, "uploads")
    os.makedirs(uploads_dir, exist_ok=True)
    safe_name = os.path.basename(filename)
    return os.path.join(uploads_dir, f"{sid}_doc_{safe_name}")


def load_session_document_texts(root: str, sid: str | None) -> list[str]:
    """掃描 uploads/<sid>_doc_* 並抽取文字，回傳非空字串清單。"""
    if not sid:
        return []
    from services.document_reader import extract_text

    uploads_dir = os.path.join(root, "uploads")
    if not os.path.isdir(uploads_dir):
        return []

    prefix = f"{sid}_doc_"
    texts: list[str] = []
    for fname in sorted(os.listdir(uploads_dir)):
        if fname.startswith(prefix):
            text = extract_text(os.path.join(uploads_dir, fname))
            if text.strip():
                texts.append(text)
    logger.info("[deps] loaded session document texts | sid=%s count=%d", sid, len(texts))
    return texts


def load_session_documents(root: str, sid: str | None) -> list[dict[str, str]]:
    """掃描 uploads/<sid>_doc_*，回傳含檔名與文字的文件資訊清單。"""
    if not sid:
        return []
    from services.document_reader import extract_text

    uploads_dir = os.path.join(root, "uploads")
    if not os.path.isdir(uploads_dir):
        return []

    prefix = f"{sid}_doc_"
    docs: list[dict[str, str]] = []
    for fname in sorted(os.listdir(uploads_dir)):
        if fname.startswith(prefix):
            text = extract_text(os.path.join(uploads_dir, fname))
            if text.strip():
                docs.append({"filename": fname, "text": text})
    logger.info("[deps] loaded session documents | sid=%s count=%d", sid, len(docs))
    return docs


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
        def _encode_sse(item: dict[str, Any]) -> str:
            seq = item.get("seq")
            if isinstance(seq, int) and seq > 0:
                return f"id: {seq}\ndata: {json.dumps(item, ensure_ascii=False)}\n\n"
            return f"data: {json.dumps(item, ensure_ascii=False)}\n\n"

        queue: asyncio.Queue = asyncio.Queue()
        task = asyncio.create_task(generator_func(queue))
        logger.debug("[deps] SSE streaming_response started")
        try:
            while True:
                if await request.is_disconnected():
                    logger.debug("[deps] SSE streaming_response disconnected")
                    task.cancel()
                    break
                try:
                    item = await asyncio.wait_for(queue.get(), timeout=0.25)
                except asyncio.TimeoutError:
                    continue
                yield _encode_sse(item)
                if item.get("type") in ("complete", "error"):
                    logger.debug("[deps] SSE streaming_response terminal event=%s", item.get("type"))
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


async def sse_streaming_detached(
    event_source: AsyncIterator[dict[str, Any]],
    request,
) -> StreamingResponse:
    """
    SSE 包裝：客戶端斷線時**不**取消 event_source 背後的背景任務。
    event_source 須為 async iterator，逐筆 yield dict；terminal 為 type complete 或 error。
    """

    async def event_stream():
        def _encode_sse(item: dict[str, Any]) -> str:
            seq = item.get("seq")
            if isinstance(seq, int) and seq > 0:
                return f"id: {seq}\ndata: {json.dumps(item, ensure_ascii=False)}\n\n"
            return f"data: {json.dumps(item, ensure_ascii=False)}\n\n"

        try:
            logger.debug("[deps] SSE detached stream started")
            async for item in event_source:
                if await request.is_disconnected():
                    logger.debug("[deps] SSE detached disconnected")
                    break
                yield _encode_sse(item)
                if item.get("type") in ("complete", "error"):
                    logger.debug("[deps] SSE detached terminal event=%s", item.get("type"))
                    break
        finally:
            aclose = getattr(event_source, "aclose", None)
            if aclose:
                with contextlib.suppress(Exception):
                    await aclose()

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )
