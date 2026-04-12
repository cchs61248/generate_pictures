"""
電商圖文助手 - 階段三：批次圖片生成

遍歷 P1~P9 JSON 腳本，依序組合 prompt 並呼叫圖像模型，
將生成結果 resize 至 1000×1000 後存入 picture/ 目錄。
"""
import asyncio
import io
import os

from PIL import Image

from core.progress import ProgressBus
from services.image_gen import generate_image_with_retry, generate_image_webapi

from api.routers.tools.ecommerce_image.prompts.image_style import prompt_template as picture_style_template
from api.routers.tools.ecommerce_image.services.image_process import build_safe_name, compose_image_prompt


def _extract_pil_image(generated_image) -> Image.Image | None:
    if isinstance(generated_image, Image.Image):
        return generated_image

    for attr in ("image", "pil_image", "_pil_image"):
        value = getattr(generated_image, attr, None)
        if isinstance(value, Image.Image):
            return value

    for attr in ("data", "bytes", "image_bytes", "_image_bytes"):
        value = getattr(generated_image, attr, None)
        if isinstance(value, (bytes, bytearray)) and value:
            image = Image.open(io.BytesIO(value))
            image.load()
            return image

    for method_name in ("to_pil", "as_pil"):
        method = getattr(generated_image, method_name, None)
        if callable(method):
            value = method()
            if isinstance(value, Image.Image):
                return value

    return None


async def generate_all_images(
    final_data: list[dict],
    image,
    image_path: str,
    picture_dir: str,
    genai_client,
    gemini_client,
    use_webapi: bool,
    use_hybrid: bool,
    session_id: str = "",
    progress: ProgressBus | None = None,
) -> list[str]:
    print("\n[階段三] 正在為每張圖生成 AI 圖片，請稍候...")
    os.makedirs(picture_dir, exist_ok=True)

    saved_files: list[str] = []
    cont = 0
    for item in final_data:
        cont += 1
        if cont >= 3:
            break
        sort_num = item["sort"]
        main_name = item["main"].replace('Prompt', '')
        image_prompt = compose_image_prompt(picture_style_template, item)
        safe_name = build_safe_name(main_name)
        group_id = f"stage3_p{sort_num:02d}"

        # 讓前端每張圖一個獨立泡泡（可摺疊工作紀錄）
        title = f"🎨 [P{sort_num:02d}] 正在生成：{main_name}..."
        print(f"\n{title}")
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
            if use_webapi or use_hybrid:
                generated_images = await generate_image_webapi(
                    gemini_client,
                    image_prompt,
                    image_path,
                )
                if not generated_images:
                    w = f"  ⚠️  P{sort_num:02d} Web API 產圖未取得圖片（Gemini 未生成圖片），跳過。"
                    print(w)
                    if progress:
                        await progress.emit(
                            {
                                "type": "collapsible_line",
                                "group_id": group_id,
                                "line": w.strip(),
                            }
                        )
                    continue
                generated_image = generated_images[0]
                raw_image = _extract_pil_image(generated_image)
                if raw_image is None:
                    attrs = [name for name in dir(generated_image) if not name.startswith("_")]
                    preview = ", ".join(attrs[:20]) if attrs else "(無可見屬性)"
                    raise ValueError(
                        f"Web API 產圖格式不支援，型別={type(generated_image).__name__}，可見屬性: {preview}"
                    )
            else:
                response = await asyncio.to_thread(
                    generate_image_with_retry,
                    genai_client,
                    image_prompt,
                    image,
                )
                for part in response.parts:
                    if part.inline_data is not None:
                        raw_image = Image.open(io.BytesIO(part.inline_data.data))
                        raw_image.load()
                        break

            if raw_image is None:
                w = f"  ⚠️  P{sort_num:02d} 未取得圖片內容，跳過。"
                print(w)
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
            print(ok_line)
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
        except Exception as exc:
            err = f"  ❌ P{sort_num:02d} 圖片生成失敗：{exc}"
            print(err)
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
        print(f"\n{done_msg}")
    else:
        done_msg = "⚠️ [階段三完成] 本次未成功儲存任何圖片。"
        print(f"\n{done_msg}")
    # 階段三收尾訊息仍用文字泡泡，避免依附在某張圖的折疊泡泡內
    if progress:
        await progress.emit({"type": "text_block", "format": "plain", "content": done_msg})

    return saved_files
