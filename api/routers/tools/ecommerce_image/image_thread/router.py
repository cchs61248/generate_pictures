"""
AI ecommerce image assistant - Image Thread (child chat) routes.
"""
import os

from fastapi import APIRouter, HTTPException, Request

from core.app_logging import get_backend_logger
from api.deps import (
    log_extra,
    new_request_id,
    project_root,
    safe_session_id,
    sse_streaming_detached,
)
from api.routers.tools.ecommerce_image.image_thread.image_thread_job import (
    cancel_image_thread_run,
    get_image_thread_status_payload,
    precheck_and_spawn_image_thread,
    subscribe_image_thread_events,
)
from api.routers.tools.ecommerce_image.image_thread.service import (
    latest_image_path_from_entries,
    load_image_thread_history,
    save_image_thread_history,
)

router = APIRouter(tags=["ecommerce-image-thread"])
logger = get_backend_logger("image_thread.router")


@router.post("/image-thread/init")
async def image_thread_init(payload: dict):
    """Store source picture path in image-thread memory for this session."""
    rid = new_request_id()
    logger.info("[image_thread/init] request received", extra=log_extra(request_id=rid))
    session_id = payload.get("session_id")
    picture_filename = payload.get("picture_filename")

    sid = safe_session_id(session_id)
    if not sid:
        logger.warning("[image_thread/init] invalid session_id", extra=log_extra(request_id=rid))
        raise HTTPException(status_code=400, detail="invalid session_id")
    if not isinstance(picture_filename, str) or not picture_filename.strip():
        logger.warning("[image_thread/init] invalid picture_filename", extra=log_extra(sid, rid))
        raise HTTPException(status_code=400, detail="invalid picture_filename")

    safe_name = os.path.basename(picture_filename.strip())
    root = project_root()
    src_path = os.path.join(root, "picture", safe_name)
    if not os.path.exists(src_path):
        logger.warning("[image_thread/init] picture not found | sid=%s filename=%s", sid, safe_name, extra=log_extra(sid, rid))
        raise HTTPException(status_code=404, detail="picture image not found")

    entries = load_image_thread_history(root, sid)
    if not any(e.get("type") == "image" for e in entries):
        entries = [{"type": "image", "path": safe_name}]
        save_image_thread_history(root, sid, entries)
        logger.info("[image_thread/init] initialized history | sid=%s image=%s", sid, safe_name, extra=log_extra(sid, rid))
    else:
        logger.info("[image_thread/init] history exists | sid=%s entries=%d", sid, len(entries), extra=log_extra(sid, rid))

    logger.info("[image_thread/init] done | sid=%s", sid, extra=log_extra(sid, rid))
    return {"ok": True, "image_path": safe_name}


@router.post("/chat/image-thread")
async def chat_image_thread(payload: dict, request: Request):
    """Run image edit in background; SSE is detached from the HTTP worker."""
    rid = new_request_id()
    logger.info("[image_thread/chat] request received", extra=log_extra(request_id=rid))
    session_id = payload.get("session_id")
    user_text = payload.get("user_text", "").strip()
    session_title = payload.get("session_title", "").strip() or "thread"
    selected_style_profile_id = payload.get("selected_style_profile_id")

    sid = safe_session_id(session_id)
    if not sid:
        logger.warning("[image_thread/chat] invalid session_id", extra=log_extra(request_id=rid))
        raise HTTPException(status_code=400, detail="invalid session_id")
    if not user_text:
        logger.warning("[image_thread/chat] user_text is empty | sid=%s", sid, extra=log_extra(sid, rid))
        raise HTTPException(status_code=400, detail="user_text is required")

    root = project_root()
    entries = load_image_thread_history(root, sid)
    latest_image_abs = latest_image_path_from_entries(root, entries)
    if not latest_image_abs or not os.path.exists(latest_image_abs):
        logger.warning("[image_thread/chat] latest image missing | sid=%s", sid, extra=log_extra(sid, rid))
        raise HTTPException(
            status_code=404,
            detail="找不到可用的圖片，請確認討論串已正確初始化。",
        )

    logger.info(
        "[image_thread/chat] spawn background run | sid=%s title=%s style_profile=%s entries=%d",
        sid,
        session_title,
        selected_style_profile_id or "(default)",
        len(entries),
        extra=log_extra(sid, rid),
    )
    await precheck_and_spawn_image_thread(
        root,
        sid,
        user_text,
        session_title,
        selected_style_profile_id,
        request_id=rid,
    )
    logger.info("[image_thread/chat] SSE subscribe attached | sid=%s", sid, extra=log_extra(sid, rid))
    return await sse_streaming_detached(
        subscribe_image_thread_events(root, sid),
        request,
    )


@router.get("/chat/image-thread/subscribe")
async def image_thread_subscribe(
    session_id: str, request: Request, from_seq: int = 0
):
    """Subscribe to image-thread job events (replay + live)."""
    logger.info("[image_thread/subscribe] request received | session_id=%s from_seq=%d", session_id, from_seq)
    root = project_root()
    sid = safe_session_id(session_id)
    if not sid:
        logger.warning("[image_thread/subscribe] invalid session_id")
        raise HTTPException(status_code=400, detail="invalid session_id")
    if from_seq < 0:
        logger.warning("[image_thread/subscribe] invalid from_seq=%d | sid=%s", from_seq, sid)
        raise HTTPException(status_code=400, detail="invalid from_seq")
    logger.info("[image_thread/subscribe] streaming | sid=%s from_seq=%d", sid, from_seq)
    return await sse_streaming_detached(
        subscribe_image_thread_events(root, sid, from_seq=from_seq),
        request,
    )


@router.get("/chat/image-thread/status")
async def image_thread_status(session_id: str):
    logger.info("[image_thread/status] request received | session_id=%s", session_id)
    root = project_root()
    sid = safe_session_id(session_id)
    if not sid:
        logger.warning("[image_thread/status] invalid session_id")
        raise HTTPException(status_code=400, detail="invalid session_id")
    logger.info("[image_thread/status] done | sid=%s", sid)
    return get_image_thread_status_payload(root, sid)


@router.post("/chat/image-thread/cancel")
async def image_thread_cancel(payload: dict):
    logger.info("[image_thread/cancel] request received")
    root = project_root()
    sid = safe_session_id(payload.get("session_id"))
    if not sid:
        logger.warning("[image_thread/cancel] invalid session_id")
        raise HTTPException(status_code=400, detail="invalid session_id")
    cancelled = await cancel_image_thread_run(root, sid)
    logger.info("[image_thread/cancel] done | sid=%s cancelled=%s", sid, cancelled)
    return {"ok": True, "cancelled": cancelled}
