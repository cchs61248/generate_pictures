import asyncio
import os

import google.genai as genai
from fastapi import APIRouter, HTTPException, Query

from core.app_logging import get_backend_logger
from api.deps import project_root, readable_error
from api.routers.tools.ecommerce_image.prompts.image_style import prompt_template as base_style_prompt
from api.routers.tools.ecommerce_image.services.style_learning import (
    delete_profile,
    delete_queue_events,
    extract_style_profile,
    list_history,
    list_queue_page,
    load_style_profile,
    rename_profile,
    restore_queue_events,
    rollback_profile,
)
from core.config import sync_managed_env_from_dotenv

router = APIRouter(tags=["ecommerce-image-style-learning"])
logger = get_backend_logger("style_learning.router")


@router.get("/tools/ecommerce-image/style-learning/status")
async def style_learning_status():
    logger.info("[style_learning/status] request received")
    root = project_root()
    queue_all = list_queue_page(root=root, page=1, page_size=1, scope="all")
    queue_pending = list_queue_page(root=root, page=1, page_size=1, scope="pending")
    queue_extracted = list_queue_page(root=root, page=1, page_size=1, scope="extracted")
    profile = load_style_profile(root)
    result = {
        "queue_total": queue_all["total"],
        "queue_pending_total": queue_pending["total"],
        "queue_extracted_total": queue_extracted["total"],
        "profile": profile,
    }
    logger.info(
        "[style_learning/status] done | total=%s pending=%s extracted=%s",
        result["queue_total"],
        result["queue_pending_total"],
        result["queue_extracted_total"],
    )
    return result


@router.get("/tools/ecommerce-image/style-learning/queue")
async def style_learning_queue(
    page: int = Query(1, ge=1),
    page_size: int = Query(10, ge=1, le=50),
    scope: str = Query("pending"),
):
    logger.info(
        "[style_learning/queue] request received | page=%d page_size=%d scope=%s",
        page,
        page_size,
        scope,
    )
    root = project_root()
    result = list_queue_page(root=root, page=page, page_size=page_size, scope=scope)
    logger.info("[style_learning/queue] done | total=%s", result.get("total"))
    return result


@router.delete("/tools/ecommerce-image/style-learning/queue")
async def style_learning_queue_delete(payload: dict):
    logger.info("[style_learning/queue delete] request received")
    ids = payload.get("event_ids")
    if not isinstance(ids, list):
        logger.warning("[style_learning/queue delete] invalid event_ids payload")
        raise HTTPException(status_code=400, detail="event_ids must be an array")
    root = project_root()
    result = delete_queue_events(root=root, event_ids=[str(i) for i in ids], actor="manual-ui")
    logger.info("[style_learning/queue delete] done | requested=%d", len(ids))
    return result


@router.post("/tools/ecommerce-image/style-learning/queue/restore")
async def style_learning_queue_restore(payload: dict):
    logger.info("[style_learning/queue restore] request received")
    ids = payload.get("event_ids")
    if not isinstance(ids, list):
        logger.warning("[style_learning/queue restore] invalid event_ids payload")
        raise HTTPException(status_code=400, detail="event_ids must be an array")
    root = project_root()
    result = restore_queue_events(root=root, event_ids=[str(i) for i in ids], actor="manual-ui")
    logger.info("[style_learning/queue restore] done | requested=%d", len(ids))
    return result


@router.get("/tools/ecommerce-image/style-learning/history")
async def style_learning_history(
    page: int = Query(1, ge=1),
    page_size: int = Query(10, ge=1, le=50),
):
    logger.info(
        "[style_learning/history] request received | page=%d page_size=%d",
        page,
        page_size,
    )
    root = project_root()
    result = list_history(root=root, page=page, page_size=page_size)
    logger.info("[style_learning/history] done | total=%s", result.get("total"))
    return result


@router.post("/tools/ecommerce-image/style-learning/extract")
async def style_learning_extract():
    logger.info("[style_learning/extract] request received")
    root = project_root()
    sync_managed_env_from_dotenv(os.path.join(root, ".env"))
    api_key = os.environ.get("GOOGLE_API_KEY") or ""
    if not api_key:
        logger.warning("[style_learning/extract] GOOGLE_API_KEY not set")
        raise HTTPException(status_code=400, detail="未設定 GOOGLE_API_KEY")

    def _extract_sync() -> dict:
        client = genai.Client(api_key=api_key)
        return extract_style_profile(
            root=root,
            genai_client=client,
            base_style_prompt=base_style_prompt,
            actor="manual-ui",
        )

    try:
        # 同步 LLM／檔案鎖會長時間佔用；若在 async 內直接呼叫會塞住整個 event loop，
        # 導致其他請求（例如設定頁載入 /settings/env）全部排隊直到萃取結束。
        result = await asyncio.to_thread(_extract_sync)
        logger.info("[style_learning/extract] done")
        return result
    except Exception as exc:
        logger.error("[style_learning/extract] failed: %s", exc)
        raise HTTPException(status_code=400, detail=readable_error(exc)) from exc


@router.post("/tools/ecommerce-image/style-learning/rollback")
async def style_learning_rollback(payload: dict):
    logger.info("[style_learning/rollback] request received")
    profile_id = payload.get("profile_id")
    if not isinstance(profile_id, str) or not profile_id.strip():
        logger.warning("[style_learning/rollback] invalid profile_id")
        raise HTTPException(status_code=400, detail="profile_id is required")
    root = project_root()
    try:
        result = rollback_profile(root=root, profile_id=profile_id, actor="manual-ui")
        logger.info("[style_learning/rollback] done | profile_id=%s", profile_id)
        return result
    except Exception as exc:
        logger.error("[style_learning/rollback] failed | profile_id=%s err=%s", profile_id, exc)
        raise HTTPException(status_code=400, detail=readable_error(exc)) from exc


@router.delete("/tools/ecommerce-image/style-learning/profile")
async def style_learning_profile_delete(payload: dict):
    logger.info("[style_learning/profile delete] request received")
    profile_id = payload.get("profile_id")
    if not isinstance(profile_id, str) or not profile_id.strip():
        logger.warning("[style_learning/profile delete] invalid profile_id")
        raise HTTPException(status_code=400, detail="profile_id is required")
    root = project_root()
    try:
        result = delete_profile(root=root, profile_id=profile_id, actor="manual-ui")
        logger.info("[style_learning/profile delete] done | profile_id=%s", profile_id)
        return result
    except Exception as exc:
        logger.error("[style_learning/profile delete] failed | profile_id=%s err=%s", profile_id, exc)
        raise HTTPException(status_code=400, detail=readable_error(exc)) from exc


@router.put("/tools/ecommerce-image/style-learning/profile")
async def style_learning_profile_rename(payload: dict):
    logger.info("[style_learning/profile rename] request received")
    profile_id = payload.get("profile_id")
    new_name = payload.get("new_name")
    if not isinstance(profile_id, str) or not profile_id.strip():
        logger.warning("[style_learning/profile rename] invalid profile_id")
        raise HTTPException(status_code=400, detail="profile_id is required")
    if not isinstance(new_name, str) or not new_name.strip():
        logger.warning("[style_learning/profile rename] invalid new_name")
        raise HTTPException(status_code=400, detail="new_name is required")
    root = project_root()
    try:
        result = rename_profile(
            root=root,
            profile_id=profile_id,
            new_name=new_name,
            actor="manual-ui",
        )
        logger.info("[style_learning/profile rename] done | profile_id=%s", profile_id)
        return result
    except Exception as exc:
        logger.error("[style_learning/profile rename] failed | profile_id=%s err=%s", profile_id, exc)
        raise HTTPException(status_code=400, detail=readable_error(exc)) from exc
