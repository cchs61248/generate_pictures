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


def filter_final_data_by_sorts(final_data: list[dict], selected_sorts: list[int]) -> list[dict]:
    """依使用者勾選的 P 編號過濾腳本；順序與 selected_sorts 唯一化後由小到大一致。"""
    if not selected_sorts:
        raise ValueError("selected_sorts 至少須指定一個 P 編號（1–9）。")
    uniq = sorted({int(s) for s in selected_sorts})
    for s in uniq:
        if s < 1 or s > 9:
            raise ValueError(f"sort 須為 1–9：{s}")
    by_sort = {int(item["sort"]): item for item in final_data}
    out: list[dict] = []
    for s in uniq:
        if s not in by_sort:
            raise ValueError(f"JSON 中找不到 sort={s} 的腳本。")
        out.append(by_sort[s])
    return out


async def run_pipeline(
    config: AppConfig,
    user_input: str | None = None,
    doc_texts: list[str] | None = None,
    doc_filenames: list[str] | None = None,
    progress: ProgressBus | None = None,
    selected_style_profile_id: str | None = None,
    image_generation_mode: str = "auto",
    selected_sorts: list[int] | None = None,
) -> dict:
    logger.info(
        "[pipeline] start run_pipeline | stage3_only=%s session_id=%s",
        config.stage3_only_mode,
        config.session_id or "(none)",
    )
    logger.debug(
        "[pipeline] inputs summary | has_user_input=%s doc_count=%d selected_style_profile_id=%s "
        "image_generation_mode=%s selected_sorts=%s",
        bool(user_input and user_input.strip()),
        len(doc_texts or []),
        selected_style_profile_id or "(default)",
        image_generation_mode,
        selected_sorts,
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
        if selected_sorts is not None:
            if len(selected_sorts) == 0:
                raise ValueError("selected_sorts 不可為空清單；略過則產出全部腳本。")
            final_data = filter_final_data_by_sorts(final_data, selected_sorts)
            logger.info("[pipeline] filtered final_data for stage3 | count=%d", len(final_data))
        if not os.path.exists(config.sample_image_path):
            logger.error("[pipeline] sample image not found: %s", config.sample_image_path)
            raise FileNotFoundError(
                f"找不到參考商品圖：{config.sample_image_path}，無法執行階段三。"
            )
        image = Image.open(config.sample_image_path)
        image.load()
        logger.debug("[pipeline] stage3_only loaded reference image: %s", config.sample_image_path)
    else:
        if user_input is None:
            user_input = input("請輸入商品描述或相關網址 (例如 https://www.apple.com/tw/mac/)：\n> ")

        logger.info("[pipeline] stage1 gather_product_info begin")
        gathered_info = await gather_product_info(
            user_input=user_input,
            image=image,
            text_provider=clients.text_provider,
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
            text_provider=clients.text_provider,
            output_json_path=config.final_output_path,
            progress=progress,
            selected_style_profile_id=selected_style_profile_id,
        )
        logger.info("[pipeline] stage2 generate_json_plan done | topics=%d", len(final_data))

        if image_generation_mode == "select":
            logger.info("[pipeline] select mode: skip stage3, await user selection")
            if progress:
                await progress.emit({"type": "plan_ready", "items": final_data})
            return {
                "final_output_path": config.final_output_path,
                "saved_files": [],
                "awaiting_stage3_selection": True,
            }

    logger.info("[pipeline] stage3 generate_all_images begin")
    saved_files = await generate_all_images(
        final_data=final_data,
        image=image,
        picture_dir=config.picture_dir,
        session_id=config.session_id,
        image_provider=clients.image_provider,
        progress=progress,
        selected_style_profile_id=selected_style_profile_id,
    )
    logger.info("[pipeline] stage3 generate_all_images done | saved=%d", len(saved_files))
    logger.info("[pipeline] end run_pipeline")

    return {
        "final_output_path": config.final_output_path,
        "saved_files": saved_files,
        "awaiting_stage3_selection": False,
    }
