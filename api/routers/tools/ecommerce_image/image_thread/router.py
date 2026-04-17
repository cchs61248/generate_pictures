"""
AI ecommerce image assistant - Image Thread (child chat) routes.
"""
import os

from fastapi import APIRouter, HTTPException, Request

from api.deps import (
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


@router.post("/image-thread/init")
async def image_thread_init(payload: dict):
    """Store source picture path in image-thread memory for this session."""
    session_id = payload.get("session_id")
    picture_filename = payload.get("picture_filename")

    sid = safe_session_id(session_id)
    if not sid:
        raise HTTPException(status_code=400, detail="invalid session_id")
    if not isinstance(picture_filename, str) or not picture_filename.strip():
        raise HTTPException(status_code=400, detail="invalid picture_filename")

    safe_name = os.path.basename(picture_filename.strip())
    root = project_root()
    src_path = os.path.join(root, "picture", safe_name)
    if not os.path.exists(src_path):
        raise HTTPException(status_code=404, detail="picture image not found")

    entries = load_image_thread_history(root, sid)
    if not any(e.get("type") == "image" for e in entries):
        entries = [{"type": "image", "path": safe_name}]
        save_image_thread_history(root, sid, entries)

    return {"ok": True, "image_path": safe_name}


@router.post("/chat/image-thread")
async def chat_image_thread(payload: dict, request: Request):
    """Run image edit in background; SSE is detached from the HTTP worker."""
    session_id = payload.get("session_id")
    user_text = payload.get("user_text", "").strip()
    session_title = payload.get("session_title", "").strip() or "thread"
    selected_style_profile_id = payload.get("selected_style_profile_id")

    sid = safe_session_id(session_id)
    if not sid:
        raise HTTPException(status_code=400, detail="invalid session_id")
    if not user_text:
        raise HTTPException(status_code=400, detail="user_text is required")

    root = project_root()
    entries = load_image_thread_history(root, sid)
    latest_image_abs = latest_image_path_from_entries(root, entries)
    if not latest_image_abs or not os.path.exists(latest_image_abs):
        raise HTTPException(
            status_code=404,
            detail="找不到可用的圖片，請確認討論串已正確初始化。",
        )

    await precheck_and_spawn_image_thread(
        root,
        sid,
        user_text,
        session_title,
        selected_style_profile_id,
    )
    return await sse_streaming_detached(
        subscribe_image_thread_events(root, sid),
        request,
    )


@router.get("/chat/image-thread/subscribe")
async def image_thread_subscribe(
    session_id: str, request: Request, from_seq: int = 0
):
    """Subscribe to image-thread job events (replay + live)."""
    root = project_root()
    sid = safe_session_id(session_id)
    if not sid:
        raise HTTPException(status_code=400, detail="invalid session_id")
    if from_seq < 0:
        raise HTTPException(status_code=400, detail="invalid from_seq")
    return await sse_streaming_detached(
        subscribe_image_thread_events(root, sid, from_seq=from_seq),
        request,
    )


@router.get("/chat/image-thread/status")
async def image_thread_status(session_id: str):
    root = project_root()
    sid = safe_session_id(session_id)
    if not sid:
        raise HTTPException(status_code=400, detail="invalid session_id")
    return get_image_thread_status_payload(root, sid)


@router.post("/chat/image-thread/cancel")
async def image_thread_cancel(payload: dict):
    root = project_root()
    sid = safe_session_id(payload.get("session_id"))
    if not sid:
        raise HTTPException(status_code=400, detail="invalid session_id")
    cancelled = await cancel_image_thread_run(root, sid)
    return {"ok": True, "cancelled": cancelled}
