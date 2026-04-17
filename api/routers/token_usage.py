"""
Token 用量查詢 Router

GET /token-usage?start=YYYY-MM-DD&end=YYYY-MM-DD
  - 無參數：回傳全部記錄
  - 有 start/end：依 timestamp 日期篩選（含邊界）
"""
from fastapi import APIRouter
from core.app_logging import get_backend_logger
from core.token_logger import read_token_usage

router = APIRouter(tags=["token-usage"])
logger = get_backend_logger("token_usage.router")


@router.get("/token-usage")
def get_token_usage(start: str | None = None, end: str | None = None):
    """取得 token 用量記錄，可用 start/end（YYYY-MM-DD）篩選日期範圍。"""
    logger.info("[token-usage] GET | start=%s end=%s", start or "", end or "")
    records = read_token_usage(start_date=start, end_date=end)
    logger.info("[token-usage] done | records=%d", len(records))
    return {"records": records}
