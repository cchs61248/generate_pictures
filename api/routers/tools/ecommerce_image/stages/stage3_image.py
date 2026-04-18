"""
電商圖文助手 - 階段三：批次圖片生成

遍歷 P1~P9 JSON 腳本，依序組合 prompt 並呼叫圖像模型，
將生成結果 resize 至 1000×1000 後存入 picture/ 目錄。
"""
import asyncio
import io
import os

from PIL import Image

from core.app_logging import get_backend_logger
from core.config import get_image_model
from core.progress import ProgressBus
from core.token_logger import log_token_usage
from services.image_gen import generate_image_with_retry

from api.routers.tools.ecommerce_image.services.image_process import build_safe_name, compose_image_prompt

logger = get_backend_logger("stages.stage3_image")


def _preview_text(text: str, limit: int = 280) -> str:
    compact = " ".join((text or "").split())
    if len(compact) <= limit:
        return compact
    return compact[:limit] + "...(truncated)"


async def generate_all_images(
    final_data: list[dict],
    image,
    picture_dir: str,
    genai_client,
    session_id: str = "",
    progress: ProgressBus | None = None,
    selected_style_profile_id: str | None = None,
) -> list[str]:
    logger.info("[stage3] enter generate_all_images")
    logger.info("[stage3] 開始批次產圖")
    logger.debug(
        "[stage3] args | items=%d picture_dir=%s session_id=%s selected_style_profile_id=%s",
        len(final_data or []),
        picture_dir,
        session_id or "(none)",
        selected_style_profile_id or "(default)",
    )
    os.makedirs(picture_dir, exist_ok=True)

    saved_files: list[str] = []
    cnt = 0
    for item in final_data:
        cnt += 1
        if cnt >= 3:
            break
        sort_num = item["sort"]
        main_name = item["main"].replace('Prompt', '')
        image_prompt = compose_image_prompt(item)
        safe_name = build_safe_name(main_name)
        group_id = f"stage3_p{sort_num:02d}"

        logger.info("[stage3] P%02d begin | main=%s", sort_num, main_name)
        logger.debug(
            "[stage3] P%02d prompt preview: %s",
            sort_num,
            _preview_text(image_prompt, 700),
        )

        # 讓前端每張圖一個獨立泡泡（可摺疊工作紀錄）
        title = f"🎨 [P{sort_num:02d}] 正在生成：{main_name}..."
        logger.info(title)
        if progress:
            await progress.emit(
                {
                    "type": "collapsible_init",
                    "group_id": group_id,
                    "title": title,
                }
            )

        try:
            raw_image = None
            logger.debug("[stage3] P%02d image generation path=api_key", sort_num)
            response = await asyncio.to_thread(
                generate_image_with_retry,
                genai_client,
                image_prompt,
                image,
                selected_style_profile_id,
            )
            usage = getattr(response, "usage_metadata", None)
            if usage is not None:
                logger.debug(
                    "[stage3] P%02d usage_metadata | prompt_tokens=%s output_tokens=%s",
                    sort_num,
                    getattr(usage, "prompt_token_count", 0) or 0,
                    getattr(usage, "candidates_token_count", 0) or 0,
                )
                try:
                    log_token_usage(
                        model=get_image_model(),
                        source="stage3_image",
                        input_tokens=getattr(usage, "prompt_token_count", 0) or 0,
                        output_tokens=getattr(usage, "candidates_token_count", 0) or 0,
                    )
                except Exception:
                    pass
            for part in response.parts:
                if part.inline_data is not None:
                    raw_image = Image.open(io.BytesIO(part.inline_data.data))
                    raw_image.load()
                    break

            if raw_image is None:
                w = f"  ⚠️  P{sort_num:02d} 未取得圖片內容，跳過。"
                logger.warning(w)
                if progress:
                    await progress.emit(
                        {
                            "type": "collapsible_line",
                            "group_id": group_id,
                            "line": w.strip(),
                        }
                    )
                continue

            resized = raw_image.resize((1000, 1000), Image.LANCZOS)
            sid_suffix = f"_{session_id}" if session_id else ""
            filename = f"P{sort_num:02d}_{safe_name}{sid_suffix}.png"
            file_path = os.path.join(picture_dir, filename)
            resized.save(file_path, "PNG")
            ok_line = f"  ✅ 已儲存（1000×1000）：{file_path}"
            logger.info(ok_line)
            logger.debug("[stage3] P%02d saved file name=%s", sort_num, filename)
            if progress:
                await progress.emit(
                    {
                        "type": "collapsible_line",
                        "group_id": group_id,
                        "line": ok_line.strip(),
                    }
                )
                # 讓前端每張圖完成就立刻新增「文字+圖片」泡泡
                await progress.emit(
                    {
                        "type": "image_saved",
                        "sort": int(sort_num),
                        "main": str(main_name),
                        "saved_file": file_path,
                    }
                )
            saved_files.append(file_path)
            logger.info("[stage3] P%02d done", sort_num)
        except Exception as exc:
            err = f"  ❌ P{sort_num:02d} 圖片生成失敗：{exc}"
            logger.error(err)
            if progress:
                await progress.emit(
                    {
                        "type": "collapsible_line",
                        "group_id": group_id,
                        "line": err.strip(),
                    }
                )
    if saved_files:
        done_msg = "✅ [階段三完成] 所有圖片已儲存至 picture/ 資料夾。"
        logger.info(done_msg)
    else:
        done_msg = "⚠️ [階段三完成] 本次未成功儲存任何圖片。"
        logger.warning(done_msg)
    # 階段三收尾訊息仍用文字泡泡，避免依附在某張圖的折疊泡泡內
    if progress:
        await progress.emit({"type": "text_block", "format": "plain", "content": done_msg})

    logger.info("[stage3] exit generate_all_images | saved=%d", len(saved_files))
    return saved_files
