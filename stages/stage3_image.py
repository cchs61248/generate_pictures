import io
import os

from PIL import Image

from prompts.image_style import prompt_template as picture_style_template
from services.image_gen import generate_image_with_retry, generate_image_webapi
from services.image_process import build_safe_name, compose_image_prompt


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
) -> list[str]:
    print("\n[階段三] 正在為每張圖生成 AI 圖片，請稍候...")
    os.makedirs(picture_dir, exist_ok=True)

    saved_files: list[str] = []
    for item in final_data:
        sort_num = item["sort"]
        main_name = item["main"]
        image_prompt = compose_image_prompt(picture_style_template, item)
        safe_name = build_safe_name(main_name)
        print(f"\n🎨 [P{sort_num:02d}] 正在生成：{main_name} ...")

        try:
            raw_image = None
            if use_webapi or use_hybrid:
                generated_images = await generate_image_webapi(
                    gemini_client,
                    image_prompt,
                    image_path,
                )
                if not generated_images:
                    print(f"  ⚠️  P{sort_num:02d} Web API 產圖未取得圖片（Gemini 未生成圖片），跳過。")
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
                response = generate_image_with_retry(genai_client, image_prompt, image)
                for part in response.parts:
                    if part.inline_data is not None:
                        raw_image = Image.open(io.BytesIO(part.inline_data.data))
                        raw_image.load()
                        break

            if raw_image is None:
                print(f"  ⚠️  P{sort_num:02d} 未取得圖片內容，跳過。")
                continue

            resized = raw_image.resize((1000, 1000), Image.LANCZOS)
            filename = f"P{sort_num:02d}_{safe_name}.png"
            file_path = os.path.join(picture_dir, filename)
            resized.save(file_path, "PNG")
            print(f"  ✅ 已儲存（1000×1000）：{file_path}")
            saved_files.append(file_path)
        except Exception as exc:
            print(f"  ❌ P{sort_num:02d} 圖片生成失敗：{exc}")
        break
    if saved_files:
        print("\n✅ [階段三完成] 所有圖片已儲存至 picture/ 資料夾。")
    else:
        print("\n⚠️ [階段三完成] 本次未成功儲存任何圖片。")

    return saved_files
