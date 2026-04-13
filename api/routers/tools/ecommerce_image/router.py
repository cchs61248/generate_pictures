"""
AI 電商圖文助手 - 主流程 Router

負責：
  - POST /run         同步執行三階段圖文生成 pipeline
  - POST /run-stream  以 SSE 串流回傳各階段進度與最終結果
"""
import asyncio
import os

from fastapi import APIRouter, HTTPException, Request

from api.deps import (
    apply_session_sample_path,
    load_session_document_texts,
    project_root,
    readable_error,
    require_session_upload_exists,
    sse_streaming_response,
)
from core.config import parse_config, sync_managed_env_from_dotenv
from core.progress import ProgressBus
from api.routers.tools.ecommerce_image.pipeline import run_pipeline

router = APIRouter(tags=["ecommerce-image"])


@router.post("/run")
async def run_generation(payload: dict):
    """同步執行三階段圖文生成 pipeline，完成後回傳結果路徑。"""
    stage3_only = bool(payload.get("stage3_only", False))
    user_input = payload.get("user_input")
    session_id = payload.get("session_id")

    root = project_root()
    sync_managed_env_from_dotenv(os.path.join(root, ".env"))
    require_session_upload_exists(session_id)
    config = parse_config(stage3_only_flag=stage3_only)
    config = apply_session_sample_path(config, session_id)
    doc_texts = load_session_document_texts(root, config.session_id or session_id)

    try:
        result = await run_pipeline(config=config, user_input=user_input, doc_texts=doc_texts)
        return {
            "ok": True,
            "final_output_path": result["final_output_path"],
            "saved_files": result["saved_files"],
        }
    except Exception as exc:
        raise HTTPException(status_code=400, detail=readable_error(exc)) from exc


@router.post("/run-stream")
async def run_generation_stream(payload: dict, request: Request):
    """以 SSE（text/event-stream）串流各階段進度與 AI 輸出片段。"""
    stage3_only = bool(payload.get("stage3_only", False))
    user_input = payload.get("user_input")
    session_id = payload.get("session_id")

    root = project_root()
    sync_managed_env_from_dotenv(os.path.join(root, ".env"))
    require_session_upload_exists(session_id)
    config = parse_config(stage3_only_flag=stage3_only)
    config = apply_session_sample_path(config, session_id)
    doc_texts = load_session_document_texts(root, config.session_id or session_id)

    async def runner(queue: asyncio.Queue):
        loop = asyncio.get_running_loop()
        bus = ProgressBus(queue, loop)
        try:
            result = await run_pipeline(
                config=config,
                user_input=user_input,
                doc_texts=doc_texts,
                progress=bus,
            )
            await queue.put(
                {
                    "type": "complete",
                    "saved_files": result["saved_files"],
                    "final_output_path": result["final_output_path"],
                }
            )
        except Exception as exc:
            await queue.put({"type": "error", "detail": readable_error(exc)})

    return await sse_streaming_response(runner, request)
