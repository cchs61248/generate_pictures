import os
from dataclasses import dataclass

import google.genai as genai

from core.app_logging import get_backend_logger
from core.config import AppConfig
from core.gemini_web_client import GeminiWebClient

logger = get_backend_logger("clients")


@dataclass
class ClientBundle:
    genai_client: object | None = None
    gemini_client: object | None = None


async def build_clients(
    config: AppConfig,
    require_text_client: bool,
    require_image_client: bool,
) -> ClientBundle:
    logger.info(
        "[clients] build start | require_text=%s require_image=%s use_webapi=%s use_hybrid=%s",
        require_text_client,
        require_image_client,
        config.use_webapi,
        config.use_hybrid,
    )
    bundle = ClientBundle()

    if require_text_client and not config.use_webapi:
        if not config.api_key:
            logger.error("[clients] missing GOOGLE_API_KEY for text client")
            raise ValueError("請在環境變數或 .env 設定 GOOGLE_API_KEY。")
        bundle.genai_client = genai.Client(api_key=config.api_key)
        logger.debug("[clients] genai text client created")

    if require_image_client and (config.use_webapi or config.use_hybrid):
        cookies_path = os.path.join(config.project_root, "cookies.json")
        logger.debug("[clients] init web client with cookies: %s", cookies_path)
        bundle.gemini_client = GeminiWebClient(cookies_path=cookies_path)
        await bundle.gemini_client.init()
        logger.debug("[clients] web client initialized")

    if require_image_client and not (config.use_webapi or config.use_hybrid):
        if bundle.genai_client is None:
            if not config.api_key:
                logger.error("[clients] missing GOOGLE_API_KEY for image API client")
                raise ValueError("請在環境變數或 .env 設定 GOOGLE_API_KEY。")
            bundle.genai_client = genai.Client(api_key=config.api_key)
            logger.debug("[clients] genai image client created")

    logger.info("[clients] build done")
    return bundle
