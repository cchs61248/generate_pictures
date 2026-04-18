"""
AI 電商圖文助手 - 主流程編排

負責串接三個階段：
  1. 階段一：gather_product_info - 蒐集商品資訊
  2. 階段二：generate_json_plan  - 產出 P1~P9 JSON 腳本
  3. 階段三：generate_all_images - 批次生成電商圖片
"""
import json
import os

from PIL import Image

from core.app_logging import get_backend_logger
from core.clients import build_clients
from core.config import AppConfig
from core.progress import ProgressBus

from api.routers.tools.ecommerce_image.stages.stage1_gather import gather_product_info
from api.routers.tools.ecommerce_image.stages.stage2_json import generate_json_plan
from api.routers.tools.ecommerce_image.stages.stage3_image import generate_all_images
from api.routers.tools.ecommerce_image.utils.json_utils import validate_output

logger = get_backend_logger("pipeline")


async def run_pipeline(
    config: AppConfig,
    user_input: str | None = None,
    doc_texts: list[str] | None = None,
    doc_filenames: list[str] | None = None,
    progress: ProgressBus | None = None,
    selected_style_profile_id: str | None = None,
) -> dict:
    logger.info(
        "[pipeline] start run_pipeline | stage3_only=%s session_id=%s",
        config.stage3_only_mode,
        config.session_id or "(none)",
    )
    logger.debug(
        "[pipeline] inputs summary | has_user_input=%s doc_count=%d selected_style_profile_id=%s",
        bool(user_input and user_input.strip()),
        len(doc_texts or []),
        selected_style_profile_id or "(default)",
    )
    require_text_client = not config.stage3_only_mode
    require_image_client = True
    clients = build_clients(config, require_text_client, require_image_client)
    logger.debug(
        "[pipeline] clients ready | require_text_client=%s require_image_client=%s",
        require_text_client,
        require_image_client,
    )

    image = None
    if not config.stage3_only_mode:
        if not os.path.exists(config.sample_image_path):
            logger.error("[pipeline] sample image not found: %s", config.sample_image_path)
            raise FileNotFoundError(f"請準備一張名為 sample.jpg 的圖片放在同目錄下：{config.sample_image_path}")
        image = Image.open(config.sample_image_path)
        image.load()
        logger.debug("[pipeline] loaded sample image: %s", config.sample_image_path)

    if config.stage3_only_mode:
        logger.info("[pipeline] enter stage3_only mode")
        if not os.path.exists(config.final_output_path):
            logger.error("[pipeline] final output JSON not found: %s", config.final_output_path)
            raise FileNotFoundError(f"找不到 {config.final_output_path}，無法執行僅階段三模式。")
        with open(config.final_output_path, "r", encoding="utf-8") as file:
            final_data = json.load(file)
        ok, reason = validate_output(final_data)
        if not ok:
            logger.error("[pipeline] final output JSON validation failed: %s", reason)
            raise ValueError(f"final_output.json 格式不正確：{reason}")
        logger.info("[pipeline] loaded final_output JSON successfully | topics=%d", len(final_data))
    else:
        if user_input is None:
            user_input = input("請輸入商品描述或相關網址 (例如 https://www.apple.com/tw/mac/)：\n> ")

        logger.info("[pipeline] stage1 gather_product_info begin")
        gathered_info = await gather_product_info(
            user_input=user_input,
            image=image,
            genai_client=clients.genai_client,
            doc_texts=doc_texts or [],
            doc_filenames=doc_filenames or [],
            progress=progress,
            selected_style_profile_id=selected_style_profile_id,
        )
        logger.info("[pipeline] stage1 gather_product_info done | chars=%d", len(gathered_info))
        logger.info("[pipeline] stage2 generate_json_plan begin")
        final_data = await generate_json_plan(
            gathered_info=gathered_info,
            image=image,
            genai_client=clients.genai_client,
            output_json_path=config.final_output_path,
            progress=progress,
            selected_style_profile_id=selected_style_profile_id,
        )
        logger.info("[pipeline] stage2 generate_json_plan done | topics=%d", len(final_data))

    logger.info("[pipeline] stage3 generate_all_images begin")
    saved_files = await generate_all_images(
        final_data=final_data,
        image=image,
        picture_dir=config.picture_dir,
        session_id=config.session_id,
        genai_client=clients.genai_client,
        progress=progress,
        selected_style_profile_id=selected_style_profile_id,
    )
    logger.info("[pipeline] stage3 generate_all_images done | saved=%d", len(saved_files))
    logger.info("[pipeline] end run_pipeline")

    return {
        "final_output_path": config.final_output_path,
        "saved_files": saved_files,
    }
