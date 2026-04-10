import asyncio
import re
import time

from google.genai import types

from core.config import get_image_model


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
    max_retries: int = 5,
):
    for attempt in range(max_retries + 1):
        try:
            return genai_client.models.generate_content(
                model=get_image_model(),
                contents=[image_prompt, product_image],
                config=types.GenerateContentConfig(
                    response_modalities=["IMAGE"],
                    image_config=types.ImageConfig(
                        aspect_ratio="1:1",
                        image_size="512",
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
    max_retries: int = 5,
) -> list:
    final_prompt = f"""請幫我生成一張電商商品圖片，圖片大小1000*1000，請參考我上傳的商品圖片外觀，依照以下設計要求生成：

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
