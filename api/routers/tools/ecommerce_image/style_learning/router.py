import os

import google.genai as genai
from fastapi import APIRouter, HTTPException, Query

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


@router.get("/tools/ecommerce-image/style-learning/status")
async def style_learning_status():
    root = project_root()
    queue_all = list_queue_page(root=root, page=1, page_size=1, scope="all")
    queue_pending = list_queue_page(root=root, page=1, page_size=1, scope="pending")
    queue_extracted = list_queue_page(root=root, page=1, page_size=1, scope="extracted")
    profile = load_style_profile(root)
    return {
        "queue_total": queue_all["total"],
        "queue_pending_total": queue_pending["total"],
        "queue_extracted_total": queue_extracted["total"],
        "profile": profile,
    }


@router.get("/tools/ecommerce-image/style-learning/queue")
async def style_learning_queue(
    page: int = Query(1, ge=1),
    page_size: int = Query(10, ge=1, le=50),
    scope: str = Query("pending"),
):
    root = project_root()
    return list_queue_page(root=root, page=page, page_size=page_size, scope=scope)


@router.delete("/tools/ecommerce-image/style-learning/queue")
async def style_learning_queue_delete(payload: dict):
    ids = payload.get("event_ids")
    if not isinstance(ids, list):
        raise HTTPException(status_code=400, detail="event_ids must be an array")
    root = project_root()
    return delete_queue_events(root=root, event_ids=[str(i) for i in ids], actor="manual-ui")


@router.post("/tools/ecommerce-image/style-learning/queue/restore")
async def style_learning_queue_restore(payload: dict):
    ids = payload.get("event_ids")
    if not isinstance(ids, list):
        raise HTTPException(status_code=400, detail="event_ids must be an array")
    root = project_root()
    return restore_queue_events(root=root, event_ids=[str(i) for i in ids], actor="manual-ui")


@router.get("/tools/ecommerce-image/style-learning/history")
async def style_learning_history(
    page: int = Query(1, ge=1),
    page_size: int = Query(10, ge=1, le=50),
):
    root = project_root()
    return list_history(root=root, page=page, page_size=page_size)


@router.post("/tools/ecommerce-image/style-learning/extract")
async def style_learning_extract():
    root = project_root()
    sync_managed_env_from_dotenv(os.path.join(root, ".env"))
    api_key = os.environ.get("GOOGLE_API_KEY") or ""
    if not api_key:
        raise HTTPException(status_code=400, detail="未設定 GOOGLE_API_KEY")
    try:
        client = genai.Client(api_key=api_key)
        return extract_style_profile(
            root=root,
            genai_client=client,
            base_style_prompt=base_style_prompt,
            actor="manual-ui",
        )
    except Exception as exc:
        raise HTTPException(status_code=400, detail=readable_error(exc)) from exc


@router.post("/tools/ecommerce-image/style-learning/rollback")
async def style_learning_rollback(payload: dict):
    profile_id = payload.get("profile_id")
    if not isinstance(profile_id, str) or not profile_id.strip():
        raise HTTPException(status_code=400, detail="profile_id is required")
    root = project_root()
    try:
        return rollback_profile(root=root, profile_id=profile_id, actor="manual-ui")
    except Exception as exc:
        raise HTTPException(status_code=400, detail=readable_error(exc)) from exc


@router.delete("/tools/ecommerce-image/style-learning/profile")
async def style_learning_profile_delete(payload: dict):
    profile_id = payload.get("profile_id")
    if not isinstance(profile_id, str) or not profile_id.strip():
        raise HTTPException(status_code=400, detail="profile_id is required")
    root = project_root()
    try:
        return delete_profile(root=root, profile_id=profile_id, actor="manual-ui")
    except Exception as exc:
        raise HTTPException(status_code=400, detail=readable_error(exc)) from exc


@router.put("/tools/ecommerce-image/style-learning/profile")
async def style_learning_profile_rename(payload: dict):
    profile_id = payload.get("profile_id")
    new_name = payload.get("new_name")
    if not isinstance(profile_id, str) or not profile_id.strip():
        raise HTTPException(status_code=400, detail="profile_id is required")
    if not isinstance(new_name, str) or not new_name.strip():
        raise HTTPException(status_code=400, detail="new_name is required")
    root = project_root()
    try:
        return rename_profile(
            root=root,
            profile_id=profile_id,
            new_name=new_name,
            actor="manual-ui",
        )
    except Exception as exc:
        raise HTTPException(status_code=400, detail=readable_error(exc)) from exc
