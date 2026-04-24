from dataclasses import dataclass, field
from typing import Any

from core.app_logging import get_backend_logger
from core.config import (
    AppConfig,
    get_image_provider,
    get_openai_api_key,
    get_openai_base_url,
    get_text_provider,
)
from core.providers.base import ImageProvider, TextProvider

logger = get_backend_logger("clients")


@dataclass
class ClientBundle:
    genai_client: Any | None = None       # 保留供需要直接使用 genai SDK 的地方
    text_provider: TextProvider | None = None
    image_provider: ImageProvider | None = None


def build_clients(
    config: AppConfig,
    require_text_client: bool,
    require_image_client: bool,
) -> ClientBundle:
    logger.info(
        "[clients] build start | require_text=%s require_image=%s",
        require_text_client,
        require_image_client,
    )
    bundle = ClientBundle()
    text_prov = get_text_provider()
    image_prov = get_image_provider()

    if require_text_client:
        if text_prov == "openai":
            api_key = get_openai_api_key()
            if not api_key:
                raise ValueError("請在環境變數或 .env 設定 OPENAI_API_KEY。")
            from core.providers.openai_compat import OpenAITextProvider
            bundle.text_provider = OpenAITextProvider(api_key, get_openai_base_url())
            logger.debug("[clients] text_provider=OpenAITextProvider")
        else:
            if not config.api_key:
                raise ValueError("請在環境變數或 .env 設定 GOOGLE_API_KEY。")
            import google.genai as genai
            genai_client = genai.Client(api_key=config.api_key)
            bundle.genai_client = genai_client
            from core.providers.gemini import GeminiTextProvider
            bundle.text_provider = GeminiTextProvider(genai_client)
            logger.debug("[clients] text_provider=GeminiTextProvider")

    if require_image_client:
        if image_prov == "openai":
            api_key = get_openai_api_key()
            if not api_key:
                raise ValueError("請在環境變數或 .env 設定 OPENAI_API_KEY。")
            from core.providers.openai_compat import OpenAIImageProvider
            bundle.image_provider = OpenAIImageProvider(api_key, get_openai_base_url())
            logger.debug("[clients] image_provider=OpenAIImageProvider")
        else:
            if not config.api_key:
                raise ValueError("請在環境變數或 .env 設定 GOOGLE_API_KEY。")
            if bundle.genai_client is None:
                import google.genai as genai
                genai_client = genai.Client(api_key=config.api_key)
                bundle.genai_client = genai_client
            from core.providers.gemini import GeminiImageProvider
            bundle.image_provider = GeminiImageProvider(bundle.genai_client)
            logger.debug("[clients] image_provider=GeminiImageProvider")

    logger.info("[clients] build done")
    return bundle
