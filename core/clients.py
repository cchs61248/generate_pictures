from dataclasses import dataclass

import google.genai as genai

from core.config import AppConfig


@dataclass
class ClientBundle:
    genai_client: object | None = None
    gemini_client: object | None = None


async def build_clients(
    config: AppConfig,
    require_text_client: bool,
    require_image_client: bool,
) -> ClientBundle:
    bundle = ClientBundle()

    if require_text_client and not config.use_webapi:
        if not config.api_key:
            raise ValueError("請在環境變數或 .env 設定 GOOGLE_API_KEY 或 GEMINI_API_KEY。")
        bundle.genai_client = genai.Client(api_key=config.api_key)

    if require_image_client and (config.use_webapi or config.use_hybrid):
        if not config.psid:
            raise ValueError(
                "請在 .env 設定 GEMINI_COOKIE_1PSID（GEMINI_BACKEND=webapi 或 hybrid 時必填）。"
            )
        from gemini_webapi import GeminiClient

        bundle.gemini_client = GeminiClient(config.psid, config.psidts, proxy=None)
        await bundle.gemini_client.init(
            timeout=30,
            auto_close=False,
            close_delay=300,
            auto_refresh=True,
        )

    if require_image_client and not (config.use_webapi or config.use_hybrid):
        if bundle.genai_client is None:
            if not config.api_key:
                raise ValueError("請在環境變數或 .env 設定 GOOGLE_API_KEY 或 GEMINI_API_KEY。")
            bundle.genai_client = genai.Client(api_key=config.api_key)

    return bundle
