"""
環境變數設定 Router

負責：
  - GET /settings/env：回傳受管理的 .env 變數列表（含模型下拉選項）
  - PUT /settings/env：寫入 .env 並立即套用至行程
"""
import os

from fastapi import APIRouter, HTTPException

from api.deps import project_root
from core.app_logging import get_backend_logger
from core.config import (
    DEFAULT_IMAGE_MODEL,
    DEFAULT_OPENAI_IMAGE_MODEL,
    DEFAULT_OPENAI_TEXT_MODEL,
    DEFAULT_TEXT_MODEL,
    ENV_VARS_HIDDEN_FROM_SETTINGS_UI,
    IMAGE_MODEL_OPTIONS,
    MANAGED_ENV_KEYS,
    MANAGED_ENV_VARS,
    PROVIDER_MODEL_CHOICES,
    TEXT_MODEL_OPTIONS,
    get_image_provider,
    get_text_provider,
    parse_env_file,
    sync_managed_env_from_dotenv,
    write_managed_env_file,
)

router = APIRouter(tags=["settings"])
logger = get_backend_logger("settings.router")


@router.get("/settings/env")
async def get_env_settings():
    logger.info("[settings] GET /settings/env")
    """回傳受管理環境變數的說明與目前值（來自專案 .env）；隱藏鍵不列出，供設定頁使用。"""
    root = project_root()
    env_path = os.path.join(root, ".env")
    parsed = parse_env_file(env_path)
    variables = []
    for spec in MANAGED_ENV_VARS:
        if spec.key in ENV_VARS_HIDDEN_FROM_SETTINGS_UI:
            continue
        val = (parsed.get(spec.key, "") or "").strip()
        if spec.key == "TEXT_MODEL":
            text_prov = (parsed.get("TEXT_PROVIDER") or "gemini").strip().lower()
            val = val or (DEFAULT_OPENAI_TEXT_MODEL if text_prov == "openai" else DEFAULT_TEXT_MODEL)
        elif spec.key == "IMAGE_MODEL":
            img_prov = (parsed.get("IMAGE_PROVIDER") or "gemini").strip().lower()
            val = val or (DEFAULT_OPENAI_IMAGE_MODEL if img_prov == "openai" else DEFAULT_IMAGE_MODEL)
        variables.append(
            {
                "key": spec.key,
                "description": spec.description,
                "value": val,
            }
        )
    # 目前供應商的模型下拉（給既有前端邏輯）
    text_prov_cur = get_text_provider()
    img_prov_cur = get_image_provider()
    model_choices = {
        "TEXT_MODEL": PROVIDER_MODEL_CHOICES["TEXT_MODEL"].get(text_prov_cur, PROVIDER_MODEL_CHOICES["TEXT_MODEL"]["gemini"]),
        "IMAGE_MODEL": PROVIDER_MODEL_CHOICES["IMAGE_MODEL"].get(img_prov_cur, PROVIDER_MODEL_CHOICES["IMAGE_MODEL"]["gemini"]),
    }
    return {
        "variables": variables,
        "modelChoices": model_choices,
        "providerModelChoices": PROVIDER_MODEL_CHOICES,
    }


@router.put("/settings/env")
async def put_env_settings(payload: dict):
    logger.info("[settings] PUT /settings/env")
    """寫入 .env 並立即套用至目前後端行程。values 可只包含要變更的鍵，其餘沿用檔案現值。"""
    raw = payload.get("values")
    if not isinstance(raw, dict):
        logger.warning("[settings] invalid values payload")
        raise HTTPException(status_code=400, detail="values must be an object")
    for k in raw:
        if k not in MANAGED_ENV_KEYS:
            logger.warning("[settings] unknown env key: %s", k)
            raise HTTPException(status_code=400, detail=f"unknown env key: {k}")

    root = project_root()
    env_path = os.path.join(root, ".env")
    parsed = parse_env_file(env_path)
    merged: dict[str, str] = {
        spec.key: parsed.get(spec.key, "") for spec in MANAGED_ENV_VARS
    }
    for key, val in raw.items():
        if val is None:
            merged[key] = ""
        elif isinstance(val, str):
            merged[key] = val
        else:
            logger.warning("[settings] invalid value type for key=%s", key)
            raise HTTPException(status_code=400, detail=f"invalid value for {key}")

    write_managed_env_file(env_path, merged)
    sync_managed_env_from_dotenv(env_path)
    logger.info("[settings] PUT done | updated_keys=%d", len(raw))
    return {"ok": True}
