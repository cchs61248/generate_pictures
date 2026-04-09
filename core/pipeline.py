import json
import os

from PIL import Image

from core.clients import build_clients
from core.config import AppConfig
from core.progress import ProgressBus
from stages.stage1_gather import gather_product_info
from stages.stage2_json import generate_json_plan
from stages.stage3_image import generate_all_images
from utils.json_utils import validate_output


async def run_pipeline(
    config: AppConfig,
    user_input: str | None = None,
    progress: ProgressBus | None = None,
) -> dict:
    require_text_client = not config.stage3_only_mode
    require_image_client = True
    clients = await build_clients(config, require_text_client, require_image_client)

    image = None
    if not config.stage3_only_mode:
        if not os.path.exists(config.sample_image_path):
            raise FileNotFoundError(f"請準備一張名為 sample.jpg 的圖片放在同目錄下：{config.sample_image_path}")
        image = Image.open(config.sample_image_path)
        image.load()

    if config.stage3_only_mode:
        if not os.path.exists(config.final_output_path):
            raise FileNotFoundError(f"找不到 {config.final_output_path}，無法執行僅階段三模式。")
        with open(config.final_output_path, "r", encoding="utf-8") as file:
            final_data = json.load(file)
        ok, reason = validate_output(final_data)
        if not ok:
            raise ValueError(f"final_output.json 格式不正確：{reason}")
    else:
        if user_input is None:
            user_input = input("請輸入商品描述或相關網址 (例如 https://www.apple.com/tw/mac/)：\n> ")

        gathered_info = await gather_product_info(
            user_input=user_input,
            image=image,
            image_path=config.sample_image_path,
            genai_client=clients.genai_client,
            gemini_client=clients.gemini_client,
            use_webapi=config.use_webapi,
            progress=progress,
        )
        final_data = await generate_json_plan(
            gathered_info=gathered_info,
            image=image,
            image_path=config.sample_image_path,
            genai_client=clients.genai_client,
            gemini_client=clients.gemini_client,
            use_webapi=config.use_webapi,
            output_json_path=config.final_output_path,
            progress=progress,
        )

    saved_files = await generate_all_images(
        final_data=final_data,
        image=image,
        image_path=config.sample_image_path,
        picture_dir=config.picture_dir,
        session_id=config.session_id,
        genai_client=clients.genai_client,
        gemini_client=clients.gemini_client,
        use_webapi=config.use_webapi,
        use_hybrid=config.use_hybrid,
        progress=progress,
    )

    return {
        "final_output_path": config.final_output_path,
        "saved_files": saved_files,
    }
