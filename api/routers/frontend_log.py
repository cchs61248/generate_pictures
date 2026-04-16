from fastapi import APIRouter, Request
from pydantic import BaseModel, Field

from core.app_logging import get_frontend_logger

router = APIRouter(tags=["frontend-log"])
logger = get_frontend_logger("api")


class FrontendLogPayload(BaseModel):
    level: str = Field(default="info", max_length=16)
    message: str = Field(min_length=1, max_length=4000)
    context: dict | None = None


@router.post("/frontend-log")
async def frontend_log(payload: FrontendLogPayload, request: Request):
    level = payload.level.strip().lower()
    msg = payload.message.strip()
    client_ip = request.client.host if request.client else "unknown"
    extra = f" | context={payload.context}" if payload.context else ""
    extra = f"{extra} | ip={client_ip}"
    text = f"{msg}{extra}"
    if level == "debug":
        logger.debug(text)
    elif level == "warning" or level == "warn":
        logger.warning(text)
    elif level == "error":
        logger.error(text)
    else:
        logger.info(text)
    return {"ok": True}
