import os
from dataclasses import dataclass

import google.genai as genai

from core.config import AppConfig
from core.gemini_web_client import GeminiWebClient


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
            raise ValueError("請在環境變數或 .env 設定 GOOGLE_API_KEY。")
        bundle.genai_client = genai.Client(api_key=config.api_key)

    if require_image_client and (config.use_webapi or config.use_hybrid):
        cookies_path = os.path.join(config.project_root, "cookies.json")
        bundle.gemini_client = GeminiWebClient(cookies_path=cookies_path)
        await bundle.gemini_client.init()

    if require_image_client and not (config.use_webapi or config.use_hybrid):
        if bundle.genai_client is None:
            if not config.api_key:
                raise ValueError("請在環境變數或 .env 設定 GOOGLE_API_KEY。")
            bundle.genai_client = genai.Client(api_key=config.api_key)

    return bundle
