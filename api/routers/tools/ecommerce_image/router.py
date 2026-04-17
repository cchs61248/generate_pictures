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

from api.deps import (
    apply_session_sample_path,
    load_session_documents,
    project_root,
    readable_error,
    require_session_upload_exists,
    safe_session_id,
    sse_streaming_detached,
)
from api.routers.tools.ecommerce_image.pipeline import run_pipeline
from api.routers.tools.ecommerce_image.services.run_job import (
    cancel_ecommerce_run,
    get_run_status_payload,
    precheck_and_spawn_run,
    subscribe_session_events,
)
from core.config import parse_config, sync_managed_env_from_dotenv
from core.progress import ProgressBus

router = APIRouter(tags=["ecommerce-image"])


@router.post("/run")
async def run_generation(payload: dict):
    """同步執行三階段圖文生成 pipeline，完成後回傳結果路徑。"""
    stage3_only = bool(payload.get("stage3_only", False))
    user_input = payload.get("user_input")
    session_id = payload.get("session_id")
    selected_style_profile_id = payload.get("selected_style_profile_id")

    root = project_root()
    sync_managed_env_from_dotenv(os.path.join(root, ".env"))
    require_session_upload_exists(session_id)
    config = parse_config(stage3_only_flag=stage3_only)
    config = apply_session_sample_path(config, session_id)
    docs = load_session_documents(root, config.session_id or session_id)
    doc_texts = [d["text"] for d in docs]
    doc_filenames = [d["filename"] for d in docs]

    try:
        result = await run_pipeline(
            config=config,
            user_input=user_input,
            doc_texts=doc_texts,
            doc_filenames=doc_filenames,
            selected_style_profile_id=selected_style_profile_id,
        )
        return {
            "ok": True,
            "final_output_path": result["final_output_path"],
            "saved_files": result["saved_files"],
        }
    except Exception as exc:
        raise HTTPException(status_code=400, detail=readable_error(exc)) from exc


@router.post("/run-stream")
async def run_generation_stream(payload: dict, request: Request):
    """以 SSE 串流各階段進度；背景執行，重新整理不會中止 pipeline。"""
    stage3_only = bool(payload.get("stage3_only", False))
    user_input = payload.get("user_input")
    session_id = payload.get("session_id")
    selected_style_profile_id = payload.get("selected_style_profile_id")

    root = project_root()
    sync_managed_env_from_dotenv(os.path.join(root, ".env"))
    require_session_upload_exists(session_id)
    sid = safe_session_id(session_id)
    if not sid:
        raise HTTPException(status_code=400, detail="invalid session_id")

    await precheck_and_spawn_run(
        root, sid, user_input, stage3_only, selected_style_profile_id
    )

    return await sse_streaming_detached(
        subscribe_session_events(root, sid),
        request,
    )


@router.get("/run-stream/subscribe")
async def run_stream_subscribe(session_id: str, request: Request):
    """僅訂閱該 session 既有 run 事件（重播 + 即時）；不要求上傳圖仍存在。"""
    root = project_root()
    sync_managed_env_from_dotenv(os.path.join(root, ".env"))
    sid = safe_session_id(session_id)
    if not sid:
        raise HTTPException(status_code=400, detail="invalid session_id")
    return await sse_streaming_detached(subscribe_session_events(root, sid), request)


@router.get("/run/status")
async def run_status(session_id: str):
    """回傳該 session 電商 run 狀態（記憶體或磁碟）。"""
    root = project_root()
    sid = safe_session_id(session_id)
    if not sid:
        raise HTTPException(status_code=400, detail="invalid session_id")
    return get_run_status_payload(root, sid)


@router.post("/run/cancel")
async def run_cancel(payload: dict):
    """取消該 session 進行中的背景 pipeline。"""
    root = project_root()
    sid = safe_session_id(payload.get("session_id"))
    if not sid:
        raise HTTPException(status_code=400, detail="invalid session_id")
    cancelled = await cancel_ecommerce_run(root, sid)
    return {"ok": True, "cancelled": cancelled}
