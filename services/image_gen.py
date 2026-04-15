import asyncio
import re
import time

from google.genai import types

from api.deps import project_root
from core.config import get_image_model, get_image_output_size
from api.routers.tools.ecommerce_image.prompts.image_style import prompt_template as picture_style_template
from api.routers.tools.ecommerce_image.services.style_learning import get_style_prompt_by_id


def resolve_picture_style_template(selected_style_profile_id: str | None = None) -> str:
    style_prompt = get_style_prompt_by_id(
        root=project_root(),
        selected_profile_id=selected_style_profile_id,
    )
    return picture_style_template + (style_prompt or "")


def is_transient_google_api_error(err_str: str) -> bool:
    lower = err_str.lower()
    return (
        "429" in err_str
        or "resource_exhausted" in lower
        or "silently aborted" in lower
        or "aborted by google" in lower
    )


def retry_wait_seconds_for_google(err_str: str) -> int:
    delay_match = re.search(r"retryDelay['\"]?\s*[:\s]+['\"]?(\d+)s", err_str, re.IGNORECASE)
    if delay_match:
        return int(delay_match.group(1)) + 5
    lower = err_str.lower()
    if "silently aborted" in lower or "aborted by google" in lower:
        return 25
    return 60


def generate_image_with_retry(
    genai_client,
    image_prompt: str,
    product_image,
    selected_style_profile_id: str | None = None,
    max_retries: int = 5,
):
    for attempt in range(max_retries + 1):
        try:
            return genai_client.models.generate_content(
                model=get_image_model(),
                contents=[image_prompt, product_image],
                config=types.GenerateContentConfig(
                    system_instruction=resolve_picture_style_template(
                        selected_style_profile_id=selected_style_profile_id
                    ),
                    response_modalities=["IMAGE"],
                    image_config=types.ImageConfig(
                        aspect_ratio="1:1",
                        image_size=get_image_output_size(),
                    ),
                ),
            )
        except Exception as exc:
            err_str = str(exc)
            if is_transient_google_api_error(err_str) and attempt < max_retries:
                wait_sec = retry_wait_seconds_for_google(err_str)
                print(f"  ⏳ 遇到暫時性錯誤，等待 {wait_sec} 秒後重試（第 {attempt + 1}/{max_retries} 次）...")
                time.sleep(wait_sec)
            else:
                raise


async def generate_image_webapi(
    gemini_client,
    image_prompt: str,
    image_path: str,
    selected_style_profile_id: str | None = None,
    max_retries: int = 5,
) -> list:
    final_prompt = f"""請幫我生成一張電商商品圖片，圖片大小1000*1000，請參考我上傳的商品圖片外觀，依照以下設計要求生成：

{resolve_picture_style_template(selected_style_profile_id=selected_style_profile_id)}

{image_prompt}
"""
    for attempt in range(max_retries + 1):
        try:
            response = await gemini_client.generate_content(
                final_prompt,
                model="gemini-3-flash-thinking",
                files=[image_path],
            )
            return response.images if response.images else []
        except Exception as exc:
            err_str = str(exc)
            if is_transient_google_api_error(err_str) and attempt < max_retries:
                wait_sec = retry_wait_seconds_for_google(err_str)
                print(f"  ⏳ Web API 產圖遇到暫時性錯誤，等待 {wait_sec} 秒後重試（第 {attempt + 1}/{max_retries} 次）...")
                await asyncio.sleep(wait_sec)
            else:
                raise
