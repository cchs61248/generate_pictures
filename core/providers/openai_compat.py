"""
OpenAI-compatible Provider — 支援 OpenAI 及任何 OpenAI-compatible API（Groq、OpenRouter、Ollama 等）。
"""
from __future__ import annotations

import asyncio
import base64
import io
import json
from typing import Any

from PIL import Image as _PILImage

from core.app_logging import get_backend_logger
from core.providers.base import (
    ContentItem,
    EditResult,
    ImageProvider,
    ImageResult,
    TextProvider,
    TextResult,
)

logger = get_backend_logger("providers.openai_compat")

_EDIT_CAPABLE_MODELS = {
    "gpt-image-2",
    "gpt-image-1.5",
    "gpt-image-1",
    "gpt-image-1-mini",
}

_OPENAI_TOOL_SCHEMAS = [
    {
        "type": "function",
        "function": {
            "name": "search_web",
            "description": "搜尋網路以取得商品最新資訊。",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "搜尋關鍵字或問題。"}
                },
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "fetch_webpage",
            "description": "抓取指定網址的頁面內容。",
            "parameters": {
                "type": "object",
                "properties": {
                    "url": {"type": "string", "description": "要抓取的完整網址。"}
                },
                "required": ["url"],
            },
        },
    },
]


def _usage_tokens(usage: Any | None) -> tuple[int, int]:
    """
    從 OpenAI SDK usage 物件取出 (input_tokens, output_tokens)。
    相容 Chat Completions（prompt/completion）與 Responses（input/output）欄位。
    """
    if usage is None:
        return 0, 0
    if isinstance(usage, dict):
        input_tokens = (
            usage.get("input_tokens")
            or usage.get("prompt_tokens")
            or 0
        )
        output_tokens = (
            usage.get("output_tokens")
            or usage.get("completion_tokens")
            or 0
        )
        return int(input_tokens or 0), int(output_tokens or 0)
    input_tokens = (
        getattr(usage, "input_tokens", None)
        or getattr(usage, "prompt_tokens", None)
        or 0
    )
    output_tokens = (
        getattr(usage, "output_tokens", None)
        or getattr(usage, "completion_tokens", None)
        or 0
    )
    return int(input_tokens or 0), int(output_tokens or 0)


def _openai_client(api_key: str, base_url: str | None):
    from openai import OpenAI
    kwargs: dict[str, Any] = {"api_key": api_key}
    # 明確指定 base_url，避免環境中 OPENAI_BASE_URL="" 時被 SDK 視為空 URL 而連線失敗。
    kwargs["base_url"] = (base_url or "https://api.openai.com/v1").strip()
    return OpenAI(**kwargs)


def _is_official_openai(base_url: str | None) -> bool:
    """True 表示使用官方 OpenAI API，支援 developer role 與 Responses API instructions 參數。"""
    if base_url is None:
        return True
    normalized = base_url.strip().rstrip("/")
    return normalized in ("", "https://api.openai.com/v1")


def _content_item_to_openai(item: ContentItem) -> dict:
    if item.type == "text":
        return {"type": "text", "text": item.text or ""}
    if item.type in ("image_pil", "image_bytes"):
        if item.type == "image_pil" and item.pil_image is not None:
            buf = io.BytesIO()
            rgb = item.pil_image.convert("RGB") if item.pil_image.mode != "RGB" else item.pil_image
            rgb.save(buf, format="JPEG")
            b64 = base64.b64encode(buf.getvalue()).decode()
            mime = "image/jpeg"
        else:
            b64 = base64.b64encode(item.image_bytes or b"").decode()
            mime = item.mime_type or "image/jpeg"
        return {"type": "image_url", "image_url": {"url": f"data:{mime};base64,{b64}"}}
    return {"type": "text", "text": ""}


def _map_image_size_openai(image_size: str) -> str:
    """將專案 image_size 字串轉換為 OpenAI images API 的 size 參數。"""
    mapping = {"512": "512x512", "1K": "1024x1024", "2K": "1024x1024", "4K": "1024x1024"}
    return mapping.get(image_size, "1024x1024")


def _parse_responses_output(output: list) -> tuple[str, bytes | None, str | None]:
    """解析 Responses API 的 output 陣列，回傳 (text, image_bytes, image_call_id)。"""
    result_text = ""
    result_image_bytes: bytes | None = None
    call_id: str | None = None
    for item in output:
        item_type = getattr(item, "type", None)
        if item_type == "message":
            content = getattr(item, "content", None) or []
            for c in content:
                c_type = getattr(c, "type", None)
                if c_type == "output_text":
                    result_text += getattr(c, "text", "") or ""
        elif item_type == "image_generation_call":
            call_id = getattr(item, "id", None)
            result_b64 = getattr(item, "result", None)
            if result_b64:
                result_image_bytes = base64.b64decode(result_b64)
    return result_text, result_image_bytes, call_id


def _extract_request_id(obj: Any) -> str | None:
    """盡力從 SDK 回傳或例外中取出 request id。"""
    if obj is None:
        return None
    rid = getattr(obj, "_request_id", None)
    if isinstance(rid, str) and rid.strip():
        return rid.strip()
    resp = getattr(obj, "response", None)
    if resp is not None:
        headers = getattr(resp, "headers", None)
        if headers:
            rid = headers.get("x-request-id") or headers.get("x-openai-request-id")
            if rid:
                return str(rid).strip()
    headers = getattr(obj, "headers", None)
    if headers:
        rid = headers.get("x-request-id") or headers.get("x-openai-request-id")
        if rid:
            return str(rid).strip()
    return None


def _friendly_image_error(exc: Exception, model: str) -> str:
    """
    將 OpenAI 圖片 API 例外轉成較可讀、可行動的錯誤訊息。
    """
    raw = str(exc or "")
    lower = raw.lower()
    if (
        "must be verified" in lower
        and "organization" in lower
        and ("gpt-image-2" in lower or model == "gpt-image-2")
    ):
        return (
            "OpenAI 帳號目前尚未完成 Organization Verification，暫時無法使用 gpt-image-2。"
            "請到 OpenAI 平台完成組織驗證（可能需等待約 15 分鐘生效），"
            "或先在設定頁把 IMAGE_MODEL 改為 gpt-image-1.5。"
        )
    return raw


class OpenAITextProvider(TextProvider):
    def __init__(self, api_key: str, base_url: str | None = None) -> None:
        self._api_key = api_key
        self._base_url = base_url

    def _client(self):
        return _openai_client(self._api_key, self._base_url)

    async def chat_with_tools(
        self,
        model: str,
        system: str,
        user_content: list[ContentItem],
        tool_fns: list,
        max_tool_calls: int,
    ) -> TextResult:
        from services.web_search import fetch_webpage, make_bounded_search_web

        bounded_search = make_bounded_search_web()

        def _run_tool(name: str, args: dict) -> str:
            if name == "search_web":
                return bounded_search(query=args.get("query", ""))
            if name == "fetch_webpage":
                return fetch_webpage(url=args.get("url", ""))
            return f"未知工具：{name}"

        openai_user_content = [_content_item_to_openai(item) for item in user_content]
        system_role = "developer" if _is_official_openai(self._base_url) else "system"
        messages: list[dict] = [
            {"role": system_role, "content": system},
            {"role": "user", "content": openai_user_content},
        ]
        client = self._client()
        total_input = 0
        total_output = 0
        calls_made = 0

        def _call() -> Any:
            return client.chat.completions.create(
                model=model,
                messages=messages,
                tools=_OPENAI_TOOL_SCHEMAS,
            )

        while True:
            response = await asyncio.to_thread(_call)
            usage = getattr(response, "usage", None)
            req_input, req_output = _usage_tokens(usage)
            total_input += req_input
            total_output += req_output

            choice = response.choices[0]
            finish_reason = getattr(choice, "finish_reason", None)
            if finish_reason != "tool_calls" or calls_made >= max_tool_calls:
                text = getattr(choice.message, "content", "") or ""
                return TextResult(text=text, input_tokens=total_input, output_tokens=total_output)

            tool_calls = getattr(choice.message, "tool_calls", None) or []
            messages.append({"role": "assistant", "content": None, "tool_calls": [
                {
                    "id": tc.id,
                    "type": "function",
                    "function": {"name": tc.function.name, "arguments": tc.function.arguments},
                }
                for tc in tool_calls
            ]})
            for tc in tool_calls:
                try:
                    args = json.loads(tc.function.arguments or "{}")
                except Exception:
                    args = {}
                result = await asyncio.to_thread(_run_tool, tc.function.name, args)
                messages.append({
                    "role": "tool",
                    "tool_call_id": tc.id,
                    "content": result,
                })
                calls_made += 1

    async def generate_text(
        self,
        model: str,
        system: str,
        user_content: list[ContentItem],
        temperature: float = 0.0,
        json_mode: bool = False,
    ) -> TextResult:
        openai_user_content = [_content_item_to_openai(item) for item in user_content]
        system_role = "developer" if _is_official_openai(self._base_url) else "system"
        messages = [
            {"role": system_role, "content": system},
            {"role": "user", "content": openai_user_content},
        ]
        kwargs: dict[str, Any] = {"model": model, "messages": messages, "temperature": temperature}
        # 注意：stage2 需求是「根節點為陣列」；chat.completions 的 json_object
        # 會強制根節點為物件，反而造成驗證失敗，因此此處不強制 json_object。
        client = self._client()
        response = await asyncio.to_thread(client.chat.completions.create, **kwargs)
        usage = getattr(response, "usage", None)
        input_tokens, output_tokens = _usage_tokens(usage)
        text = response.choices[0].message.content or ""
        return TextResult(
            text=text,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
        )


class OpenAIImageProvider(ImageProvider):
    def __init__(self, api_key: str, base_url: str | None = None) -> None:
        self._api_key = api_key
        self._base_url = base_url

    def _client(self):
        return _openai_client(self._api_key, self._base_url)

    async def generate_image(
        self,
        model: str,
        prompt: str,
        reference_image_pil: Any,
        style_instruction: str,
        image_size: str,
    ) -> ImageResult:
        full_prompt = f"{style_instruction}\n\n{prompt}" if style_instruction else prompt
        size = _map_image_size_openai(image_size)
        client = self._client()

        if model in _EDIT_CAPABLE_MODELS:
            buf = io.BytesIO()
            # images.edit 在部分路徑要求輸入含 alpha 或灰階通道，統一轉 RGBA 避免 RGB 被拒。
            rgba = reference_image_pil.convert("RGBA") if reference_image_pil.mode != "RGBA" else reference_image_pil
            rgba.save(buf, format="PNG")
            image_bytes = buf.getvalue()

            def _edit() -> Any:
                # 目前實測本環境 images.edit 要求 image 為單一檔案，而非陣列。
                kwargs: dict[str, Any] = {
                    "model": model,
                    "image": ("reference.png", image_bytes, "image/png"),
                    "prompt": full_prompt,
                    "size": size,
                }
                # GPT Image 系列在新版路徑通常不需要（也不建議）顯式帶 response_format，
                # 避免被路由到舊相容驗證分支（常見錯誤：model must be dall-e-2）。
                if model == "dall-e-2":
                    kwargs["response_format"] = "b64_json"
                # 備註：部分 API 版本對 input_fidelity 等新參數尚未開放，
                # 先用最小相容參數集，避免 unknown_parameter 400。
                return client.images.edit(**kwargs)
            logger.info(
                "[openai_compat] images.edit request | model=%s size=%s prompt_len=%d image_count=%d payload_keys=%s",
                model,
                size,
                len(full_prompt),
                1,
                ",".join(sorted(["model", "image", "prompt", "size"] + (["response_format"] if model == "dall-e-2" else []))),
            )
            try:
                response = await asyncio.to_thread(_edit)
                logger.info(
                    "[openai_compat] images.edit response | model=%s request_id=%s",
                    model,
                    _extract_request_id(response) or "(none)",
                )
            except Exception as exc:
                logger.warning(
                    "[openai_compat] images.edit error | model=%s request_id=%s err=%s",
                    model,
                    _extract_request_id(exc) or "(none)",
                    str(exc),
                )
                raise RuntimeError(_friendly_image_error(exc, model)) from exc
        else:
            quality = "hd" if image_size in ("2K", "4K") else "standard"

            def _generate() -> Any:
                return client.images.generate(
                    model=model,
                    prompt=full_prompt,
                    size=size,
                    quality=quality,
                    response_format="b64_json",
                )

            try:
                response = await asyncio.to_thread(_generate)
            except Exception as exc:
                logger.warning(
                    "[openai_compat] images.generate error | model=%s request_id=%s err=%s",
                    model,
                    _extract_request_id(exc) or "(none)",
                    str(exc),
                )
                raise RuntimeError(_friendly_image_error(exc, model)) from exc

        b64 = response.data[0].b64_json
        image_bytes = base64.b64decode(b64)
        usage = getattr(response, "usage", None)
        input_tokens, output_tokens = _usage_tokens(usage)
        return ImageResult(
            image_bytes=image_bytes,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
        )

    async def edit_image(
        self,
        model: str,
        image_bytes: bytes,
        prompt: str,
        style_instruction: str,
        image_size: str,
        previous_provider_state: dict | None = None,
    ) -> EditResult:
        client = self._client()

        # gpt-image-2 等圖像專用模型不被 Responses API 接受，改走 images.edit() 路徑。
        if model in _EDIT_CAPABLE_MODELS:
            pil_img = _PILImage.open(io.BytesIO(image_bytes))
            rgba = pil_img.convert("RGBA") if pil_img.mode != "RGBA" else pil_img
            buf = io.BytesIO()
            rgba.save(buf, format="PNG")
            png_bytes = buf.getvalue()
            full_prompt = f"{style_instruction}\n\n{prompt}" if style_instruction else prompt
            size = _map_image_size_openai(image_size)

            def _edit_capable() -> Any:
                kwargs: dict[str, Any] = {
                    "model": model,
                    "image": ("reference.png", png_bytes, "image/png"),
                    "prompt": full_prompt,
                    "size": size,
                }
                return client.images.edit(**kwargs)

            logger.info(
                "[openai_compat] edit_image images.edit path | model=%s size=%s prompt_len=%d",
                model, size, len(full_prompt),
            )
            try:
                response = await asyncio.to_thread(_edit_capable)
            except Exception as exc:
                logger.warning(
                    "[openai_compat] edit_image images.edit error | model=%s err=%s", model, str(exc)
                )
                raise RuntimeError(_friendly_image_error(exc, model)) from exc

            b64 = response.data[0].b64_json
            result_bytes = base64.b64decode(b64)
            usage = getattr(response, "usage", None)
            input_tokens, output_tokens = _usage_tokens(usage)
            return EditResult(
                text="",
                image_bytes=result_bytes,
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                provider_state=None,
            )

        use_instructions = _is_official_openai(self._base_url)

        # 官方 OpenAI：style_instruction 透過 instructions 頂層參數傳入，prompt 保持純粹。
        # 自訂端點：instructions 參數不保證支援，改用字串拼接維持相容性。
        if use_instructions:
            first_turn_text = prompt
        else:
            full_prompt = f"{style_instruction}\n\n{prompt}" if style_instruction else prompt
            first_turn_text = full_prompt

        if previous_provider_state and previous_provider_state.get("image_call_id"):
            input_payload: list = [
                {
                    "type": "image_generation_call",
                    "id": previous_provider_state["image_call_id"],
                },
                {
                    "role": "user",
                    "content": [{"type": "input_text", "text": prompt}],
                },
            ]
        else:
            b64_ref = base64.b64encode(image_bytes).decode()
            input_payload = [
                {
                    "role": "user",
                    "content": [
                        {"type": "input_text", "text": first_turn_text},
                        {"type": "input_image", "image_url": f"data:image/jpeg;base64,{b64_ref}"},
                    ],
                }
            ]

        def _create() -> Any:
            kwargs: dict[str, Any] = {
                "model": model,
                "input": input_payload,
                "tools": [{"type": "image_generation"}],
            }
            if use_instructions and style_instruction:
                kwargs["instructions"] = style_instruction
            return client.responses.create(**kwargs)

        response = await asyncio.to_thread(_create)
        output = getattr(response, "output", None) or []
        result_text, result_image_bytes, call_id = _parse_responses_output(output)
        usage = getattr(response, "usage", None)
        input_tokens, output_tokens = _usage_tokens(usage)
        return EditResult(
            text=result_text,
            image_bytes=result_image_bytes,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            provider_state={"image_call_id": call_id} if call_id else None,
        )
