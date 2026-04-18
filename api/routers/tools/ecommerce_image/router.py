"""
AI 電商圖文助手 - 主流程 Router

負責：
  - POST /run         同步執行三階段圖文生成 pipeline
  - POST /run-stream  以 SSE 串流回傳各階段進度與最終結果（背景執行，斷線不中斷）
  - GET  /run-stream/subscribe  僅訂閱既有任務事件（重新整理後重連）
  - GET  /run/status            任務狀態（供前端 purge / 重連判斷）
  - POST /run/cancel            取消進行中任務
"""
import os

from fastapi import APIRouter, HTTPException, Request

from core.app_logging import get_backend_logger
from api.deps import (
    apply_session_sample_path,
    load_session_documents,
    log_extra,
    new_request_id,
    project_root,
    readable_error,
    require_session_upload_exists,
    safe_session_id,
    sse_streaming_detached,
)
from api.routers.tools.ecommerce_image.pipeline import run_pipeline
from api.routers.tools.ecommerce_image.services.run_job import (
    awaiting_stage3_from_events,
    cancel_ecommerce_run,
    get_run_status_payload,
    parse_selected_sorts_payload,
    precheck_and_spawn_run,
    read_run_job_disk,
    subscribe_session_events,
)
from core.config import parse_config, sync_managed_env_from_dotenv

router = APIRouter(tags=["ecommerce-image"])
logger = get_backend_logger("ecommerce.router")


def _parse_run_payload(payload: dict) -> tuple[str, list[int] | None]:
    raw_mode = payload.get("image_generation_mode") or "auto"
    image_generation_mode = raw_mode if raw_mode in ("auto", "select") else "auto"
    try:
        selected_sorts = parse_selected_sorts_payload(payload.get("selected_sorts"))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    stage3_only = bool(payload.get("stage3_only", False))
    if stage3_only and selected_sorts is not None and len(selected_sorts) == 0:
        raise HTTPException(
            status_code=400,
            detail="selected_sorts 不可為空清單；若要產出全部腳本請省略該欄位。",
        )
    if not stage3_only and selected_sorts is not None:
        raise HTTPException(
            status_code=400,
            detail="selected_sorts 僅能與 stage3_only 一併使用。",
        )
    return image_generation_mode, selected_sorts


@router.post("/run")
async def run_generation(payload: dict):
    rid = new_request_id()
    logger.info("[run] request received", extra=log_extra(request_id=rid))
    """同步執行三階段圖文生成 pipeline，完成後回傳結果路徑。"""
    stage3_only = bool(payload.get("stage3_only", False))
    user_input = payload.get("user_input")
    session_id = payload.get("session_id")
    selected_style_profile_id = payload.get("selected_style_profile_id")
    image_generation_mode, selected_sorts = _parse_run_payload(payload)

    root = project_root()
    sync_managed_env_from_dotenv(os.path.join(root, ".env"))
    require_session_upload_exists(session_id)
    config = parse_config(stage3_only_flag=stage3_only)
    config = apply_session_sample_path(config, session_id)
    docs = load_session_documents(root, config.session_id or session_id)
    doc_texts = [d["text"] for d in docs]
    doc_filenames = [d["filename"] for d in docs]

    try:
        logger.info(
            "[run] execute pipeline | sid=%s stage3_only=%s docs=%d style_profile=%s",
            config.session_id or "(none)",
            stage3_only,
            len(docs),
            selected_style_profile_id or "(default)",
            extra=log_extra(config.session_id or None, rid),
        )
        result = await run_pipeline(
            config=config,
            user_input=user_input,
            doc_texts=doc_texts,
            doc_filenames=doc_filenames,
            selected_style_profile_id=selected_style_profile_id,
            image_generation_mode=image_generation_mode,
            selected_sorts=selected_sorts,
        )
        return {
            "ok": True,
            "final_output_path": result["final_output_path"],
            "saved_files": result["saved_files"],
            "awaiting_stage3_selection": bool(result.get("awaiting_stage3_selection")),
        }
    except Exception as exc:
        logger.error("[run] failed: %s", exc, extra=log_extra(config.session_id or None, rid))
        raise HTTPException(status_code=400, detail=readable_error(exc)) from exc


@router.post("/run-stream")
async def run_generation_stream(payload: dict, request: Request):
    rid = new_request_id()
    logger.info("[run-stream] request received", extra=log_extra(request_id=rid))
    """以 SSE 串流各階段進度；背景執行，重新整理不會中止 pipeline。"""
    stage3_only = bool(payload.get("stage3_only", False))
    user_input = payload.get("user_input")
    session_id = payload.get("session_id")
    selected_style_profile_id = payload.get("selected_style_profile_id")
    image_generation_mode, selected_sorts = _parse_run_payload(payload)

    root = project_root()
    sync_managed_env_from_dotenv(os.path.join(root, ".env"))
    require_session_upload_exists(session_id)
    sid = safe_session_id(session_id)
    if not sid:
        raise HTTPException(status_code=400, detail="invalid session_id")

    await precheck_and_spawn_run(
        root,
        sid,
        user_input,
        stage3_only,
        selected_style_profile_id,
        request_id=rid,
        image_generation_mode=image_generation_mode,
        selected_sorts=selected_sorts,
    )
    logger.info(
        "[run-stream] subscribed | sid=%s stage3_only=%s style_profile=%s mode=%s",
        sid,
        stage3_only,
        selected_style_profile_id or "(default)",
        image_generation_mode,
        extra=log_extra(sid, rid),
    )

    return await sse_streaming_detached(
        subscribe_session_events(root, sid),
        request,
    )


@router.get("/run-stream/subscribe")
async def run_stream_subscribe(
    session_id: str, request: Request, from_seq: int = 0
):
    logger.info("[run-stream/subscribe] request | session_id=%s from_seq=%d", session_id, from_seq)
    """僅訂閱該 session 既有 run 事件（重播 + 即時）；不要求上傳圖仍存在。"""
    root = project_root()
    sync_managed_env_from_dotenv(os.path.join(root, ".env"))
    sid = safe_session_id(session_id)
    if not sid:
        raise HTTPException(status_code=400, detail="invalid session_id")
    if from_seq < 0:
        raise HTTPException(status_code=400, detail="invalid from_seq")
    return await sse_streaming_detached(
        subscribe_session_events(root, sid, from_seq=from_seq), request
    )


@router.get("/run/status")
async def run_status(session_id: str):
    logger.info("[run/status] request | session_id=%s", session_id)
    """回傳該 session 電商 run 狀態（記憶體或磁碟）。"""
    root = project_root()
    sid = safe_session_id(session_id)
    if not sid:
        raise HTTPException(status_code=400, detail="invalid session_id")
    return get_run_status_payload(root, sid)


@router.get("/run/awaiting-plan")
async def run_awaiting_plan(session_id: str):
    """
    若最近一次 run 結束於「待選圖」，回傳 plan_ready 的 items（供前端在無 localStorage 時還原）。
    """
    root = project_root()
    sid = safe_session_id(session_id)
    if not sid:
        raise HTTPException(status_code=400, detail="invalid session_id")
    disk = read_run_job_disk(root, sid)
    if not disk:
        return {"awaiting": False, "items": None}
    events = disk.get("events") or []
    if not isinstance(events, list):
        return {"awaiting": False, "items": None}

    if not awaiting_stage3_from_events(events):
        return {"awaiting": False, "items": None}
    items = None
    for ev in reversed(events):
        if isinstance(ev, dict) and ev.get("type") == "plan_ready":
            raw = ev.get("items")
            if isinstance(raw, list):
                items = raw
            break
    if not items:
        return {"awaiting": False, "items": None}
    return {"awaiting": True, "items": items}


@router.post("/run/cancel")
async def run_cancel(payload: dict):
    logger.info("[run/cancel] request received")
    """取消該 session 進行中的背景 pipeline。"""
    root = project_root()
    sid = safe_session_id(payload.get("session_id"))
    if not sid:
        raise HTTPException(status_code=400, detail="invalid session_id")
    cancelled = await cancel_ecommerce_run(root, sid)
    logger.info("[run/cancel] done | sid=%s cancelled=%s", sid, cancelled)
    return {"ok": True, "cancelled": cancelled}
