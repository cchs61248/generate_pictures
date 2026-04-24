"""
Token 用量紀錄模組

提供 log_token_usage() 供各 Gemini API key 呼叫點使用。
每筆記錄寫入 data/token_usage.json，thread-safe。
"""
import json
import os
import threading
from datetime import datetime

from core.app_logging import get_backend_logger
from core.config import resolve_project_root

_lock = threading.Lock()
logger = get_backend_logger("token_logger")


def _data_dir() -> str:
    return os.path.join(resolve_project_root(), "data")


def _usage_file() -> str:
    return os.path.join(_data_dir(), "token_usage.json")


def log_token_usage(
    model: str,
    source: str,
    input_tokens: int,
    output_tokens: int,
) -> None:
    """
    將一筆 token 用量記錄附加寫入 data/token_usage.json。

    Args:
        model:         呼叫的模型名稱（如 "gemini-2.0-flash"）
        source:        呼叫來源識別字串（如 "stage1_gather"）
        input_tokens:  輸入 token 數
        output_tokens: 輸出 token 數
    """
    record = {
        "timestamp": datetime.now().isoformat(),
        "model": model,
        "source": source,
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
    }
    path = _usage_file()
    logger.debug(
        "[token_logger] append usage | model=%s source=%s input=%d output=%d",
        model,
        source,
        input_tokens,
        output_tokens,
    )
    with _lock:
        os.makedirs(_data_dir(), exist_ok=True)
        if os.path.exists(path):
            try:
                with open(path, "r", encoding="utf-8") as f:
                    records = json.load(f)
                if not isinstance(records, list):
                    records = []
            except Exception:
                logger.warning("[token_logger] usage file broken; reset list")
                records = []
        else:
            records = []
        records.append(record)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(records, f, ensure_ascii=False, indent=2)
    logger.debug("[token_logger] append done | total_records=%d", len(records))


def read_token_usage(
    start_date: str | None = None,
    end_date: str | None = None,
) -> list[dict]:
    """
    讀取 data/token_usage.json，可依日期（YYYY-MM-DD）篩選。

    Args:
        start_date: 起始日期字串（含），None 則不限
        end_date:   結束日期字串（含），None 則不限

    Returns:
        符合條件的記錄列表
    """
    path = _usage_file()
    if not os.path.exists(path):
        logger.debug("[token_logger] read skipped; file not found")
        return []
    with _lock:
        try:
            with open(path, "r", encoding="utf-8") as f:
                records = json.load(f)
            if not isinstance(records, list):
                return []
        except Exception:
            logger.warning("[token_logger] read failed; return empty list")
            return []

    if not start_date and not end_date:
        logger.debug("[token_logger] read done | records=%d no date filter", len(records))
        return records

    filtered = []
    for r in records:
        ts = r.get("timestamp", "")
        date_str = ts[:10]  # YYYY-MM-DD
        if start_date and date_str < start_date:
            continue
        if end_date and date_str > end_date:
            continue
        filtered.append(r)
    logger.debug(
        "[token_logger] read done | records=%d filtered=%d start=%s end=%s",
        len(records),
        len(filtered),
        start_date or "",
        end_date or "",
    )
    return filtered
