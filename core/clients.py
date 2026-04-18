from dataclasses import dataclass

import google.genai as genai

from core.app_logging import get_backend_logger
from core.config import AppConfig

logger = get_backend_logger("clients")


@dataclass
class ClientBundle:
    genai_client: object | None = None


def build_clients(
    config: AppConfig,
    require_text_client: bool,
    require_image_client: bool,
) -> ClientBundle:
    need_genai = require_text_client or require_image_client
    logger.info(
        "[clients] build start | require_text=%s require_image=%s",
        require_text_client,
        require_image_client,
    )
    bundle = ClientBundle()
    if need_genai:
        if not config.api_key:
            logger.error("[clients] missing GOOGLE_API_KEY")
            raise ValueError("請在環境變數或 .env 設定 GOOGLE_API_KEY。")
        bundle.genai_client = genai.Client(api_key=config.api_key)
        logger.debug("[clients] genai client created")
    logger.info("[clients] build done")
    return bundle
