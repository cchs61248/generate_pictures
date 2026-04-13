"""
Token 用量查詢 Router

GET /token-usage?start=YYYY-MM-DD&end=YYYY-MM-DD
  - 無參數：回傳全部記錄
  - 有 start/end：依 timestamp 日期篩選（含邊界）
"""
from fastapi import APIRouter
from core.token_logger import read_token_usage

router = APIRouter(tags=["token-usage"])


@router.get("/token-usage")
def get_token_usage(start: str | None = None, end: str | None = None):
    """取得 token 用量記錄，可用 start/end（YYYY-MM-DD）篩選日期範圍。"""
    records = read_token_usage(start_date=start, end_date=end)
    return {"records": records}
