"""
Gemini Provider — 將現有 google.genai SDK 呼叫封裝為 TextProvider / ImageProvider。
"""
from __future__ import annotations

import asyncio
import io
import time
from typing import Any

from google.genai import types as genai_types

from core.app_logging import get_backend_logger
from core.providers.base import (
    ContentItem,
    EditResult,
    ImageProvider,
    ImageResult,
    TextProvider,
    TextResult,
)

logger = get_backend_logger("providers.gemini")


def _pil_to_bytes(pil_image: Any, fmt: str = "JPEG") -> bytes:
    buf = io.BytesIO()
    rgb = pil_image.convert("RGB") if pil_image.mode != "RGB" else pil_image
    rgb.save(buf, format=fmt)
    return buf.getvalue()


def _build_gemini_contents(user_content: list[ContentItem]) -> list:
    parts: list = []
    for item in user_content:
        if item.type == "text" and item.text:
            parts.append(item.text)
        elif item.type == "image_pil" and item.pil_image is not None:
            parts.append(item.pil_image)
        elif item.type == "image_bytes" and item.image_bytes is not None:
            from PIL import Image
            img = Image.open(io.BytesIO(item.image_bytes))
            img.load()
            parts.append(img)
    return parts


def _response_text_safe(response: Any) -> str:
    candidates = getattr(response, "candidates", None)
    if candidates:
        text_parts: list[str] = []
        for cand in candidates:
            content = getattr(cand, "content", None)
            parts = getattr(content, "parts", None) if content else None
            if not parts:
                continue
            for part in parts:
                text = getattr(part, "text", None)
                if isinstance(text, str) and text:
                    text_parts.append(text)
        if text_parts:
            return "\n".join(text_parts).strip()
    return getattr(response, "text", "") or ""


def _is_transient_google_error(err_str: str) -> bool:
    lower = err_str.lower()
    return (
        "429" in err_str
        or "resource_exhausted" in lower
        or "silently aborted" in lower
        or "aborted by google" in lower
    )


def _retry_wait_secs(err_str: str) -> int:
    import re
    m = re.search(r"retryDelay['\"]?\s*[:\s]+['\"]?(\d+)s", err_str, re.IGNORECASE)
    if m:
        return int(m.group(1)) + 5
    lower = err_str.lower()
    if "silently aborted" in lower or "aborted by google" in lower:
        return 25
    return 60


class GeminiTextProvider(TextProvider):
    def __init__(self, genai_client: Any) -> None:
        self._client = genai_client

    async def chat_with_tools(
        self,
        model: str,
        system: str,
        user_content: list[ContentItem],
        tool_fns: list,
        max_tool_calls: int,
    ) -> TextResult:
        from services.web_search import get_max_llm_search_calls

        chat = self._client.chats.create(
            model=model,
            config=genai_types.GenerateContentConfig(
                tools=tool_fns,
                system_instruction=system,
                automatic_function_calling=genai_types.AutomaticFunctionCallingConfig(
                    disable=False,
                    maximum_remote_calls=get_max_llm_search_calls() + 2,
                ),
            ),
        )
        contents = _build_gemini_contents(user_content)
        response = await asyncio.to_thread(chat.send_message, contents)
        usage = getattr(response, "usage_metadata", None)
        return TextResult(
            text=_response_text_safe(response),
            input_tokens=getattr(usage, "prompt_token_count", 0) or 0 if usage else 0,
            output_tokens=getattr(usage, "candidates_token_count", 0) or 0 if usage else 0,
        )

    async def generate_text(
        self,
        model: str,
        system: str,
        user_content: list[ContentItem],
        temperature: float = 0.0,
        json_mode: bool = False,
    ) -> TextResult:
        contents = _build_gemini_contents(user_content)
        cfg = genai_types.GenerateContentConfig(
            system_instruction=system,
            temperature=temperature,
            **({"response_mime_type": "application/json"} if json_mode else {}),
        )
        response = await asyncio.to_thread(
            self._client.models.generate_content,
            model=model,
            contents=contents,
            config=cfg,
        )
        usage = getattr(response, "usage_metadata", None)
        return TextResult(
            text=getattr(response, "text", "") or "",
            input_tokens=getattr(usage, "prompt_token_count", 0) or 0 if usage else 0,
            output_tokens=getattr(usage, "candidates_token_count", 0) or 0 if usage else 0,
        )


class GeminiImageProvider(ImageProvider):
    def __init__(self, genai_client: Any) -> None:
        self._client = genai_client

    def _generate_sync(
        self,
        model: str,
        prompt: str,
        pil_image: Any,
        style_instruction: str,
        image_size: str,
        max_retries: int = 5,
    ) -> Any:
        for attempt in range(max_retries + 1):
            try:
                return self._client.models.generate_content(
                    model=model,
                    contents=[prompt, pil_image],
                    config=genai_types.GenerateContentConfig(
                        system_instruction=style_instruction,
                        response_modalities=["IMAGE"],
                        image_config=genai_types.ImageConfig(
                            aspect_ratio="1:1",
                            image_size=image_size,
                        ),
                    ),
                )
            except Exception as exc:
                err_str = str(exc)
                if _is_transient_google_error(err_str) and attempt < max_retries:
                    wait = _retry_wait_secs(err_str)
                    logger.warning(
                        "[gemini] transient error, retry %d/%d after %ds",
                        attempt + 1, max_retries, wait,
                    )
                    time.sleep(wait)
                else:
                    raise

    async def generate_image(
        self,
        model: str,
        prompt: str,
        reference_image_pil: Any,
        style_instruction: str,
        image_size: str,
    ) -> ImageResult:
        response = await asyncio.to_thread(
            self._generate_sync,
            model, prompt, reference_image_pil, style_instruction, image_size,
        )
        usage = getattr(response, "usage_metadata", None)
        for part in getattr(response, "parts", None) or []:
            if getattr(part, "inline_data", None) is not None:
                return ImageResult(
                    image_bytes=part.inline_data.data,
                    input_tokens=getattr(usage, "prompt_token_count", 0) or 0 if usage else 0,
                    output_tokens=getattr(usage, "candidates_token_count", 0) or 0 if usage else 0,
                )
        raise RuntimeError("Gemini generate_image: 未收到圖片內容。")

    async def edit_image(
        self,
        model: str,
        image_bytes: bytes,
        prompt: str,
        style_instruction: str,
        image_size: str,
        previous_provider_state: dict | None = None,
    ) -> EditResult:
        from PIL import Image

        img = Image.open(io.BytesIO(image_bytes))
        img.load()
        mime = "image/jpeg"

        contents = [
            genai_types.Content(
                role="user",
                parts=[
                    genai_types.Part.from_bytes(data=image_bytes, mime_type=mime),
                    genai_types.Part.from_text(text=prompt),
                ],
            )
        ]

        response = await asyncio.to_thread(
            self._client.models.generate_content,
            model=model,
            contents=contents,
            config=genai_types.GenerateContentConfig(
                system_instruction=style_instruction,
                response_modalities=["TEXT", "IMAGE"],
                image_config=genai_types.ImageConfig(
                    aspect_ratio="1:1",
                    image_size=image_size,
                ),
            ),
        )
        usage = getattr(response, "usage_metadata", None)
        result_text = ""
        result_image_bytes: bytes | None = None
        for part in getattr(response, "parts", None) or []:
            if hasattr(part, "text") and part.text:
                result_text += part.text
            elif hasattr(part, "inline_data") and part.inline_data is not None:
                result_image_bytes = part.inline_data.data
        return EditResult(
            text=result_text,
            image_bytes=result_image_bytes,
            input_tokens=getattr(usage, "prompt_token_count", 0) or 0 if usage else 0,
            output_tokens=getattr(usage, "candidates_token_count", 0) or 0 if usage else 0,
            provider_state=None,
        )
