"""
Image Thread 歷史管理服務

負責 image thread 對話記憶的讀、寫、刪除以及最新圖片路徑查詢。

記憶格式（entries 陣列，每筆一條記錄）：
  { "type": "image",  "path": "<picture 目錄內的相對路徑>" }
  { "type": "user",   "text": "<使用者指令>" }
  { "type": "model",  "text": "<LLM 說明文字>", "image_path": "<picture 內路徑 or null>" }

策略：
  - init 時寫入第一條 image entry（原始圖片）
  - 每次對話從 entries 末端往前找最新的 image path（init 圖 or 上輪 model 產出圖）
  - LLM 產出圖後，存入 picture/，並在 model entry 記錄 image_path
"""
import json
import os

from core.app_logging import get_backend_logger

logger = get_backend_logger("image_thread.service")


def _history_path(root: str, session_id: str) -> str:
    data_dir = os.path.join(root, "data")
    os.makedirs(data_dir, exist_ok=True)
    return os.path.join(data_dir, f"image_thread_history_{session_id}.json")


def load_image_thread_history(root: str, session_id: str) -> list[dict]:
    """讀取指定 session 的 image thread 歷史；不存在或損毀時回傳空列表。"""
    path = _history_path(root, session_id)
    if not os.path.exists(path):
        logger.debug("[image_thread/service] history not found | sid=%s", session_id)
        return []
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, list):
            logger.debug("[image_thread/service] history loaded | sid=%s entries=%d", session_id, len(data))
            return data
    except Exception as exc:
        logger.warning("[image_thread/service] history load failed | sid=%s err=%s", session_id, exc)
    return []


def save_image_thread_history(root: str, session_id: str, entries: list[dict]) -> None:
    """將 image thread 歷史寫入磁碟（atomic write）。"""
    path = _history_path(root, session_id)
    temp_path = f"{path}.tmp"
    with open(temp_path, "w", encoding="utf-8") as f:
        json.dump(entries, f, ensure_ascii=False)
    os.replace(temp_path, path)
    logger.debug("[image_thread/service] history saved | sid=%s entries=%d", session_id, len(entries))


def delete_image_thread_history(root: str, session_id: str) -> bool:
    """刪除指定 session 的 image thread 歷史；不存在時回傳 False。"""
    path = _history_path(root, session_id)
    if os.path.exists(path):
        os.remove(path)
        logger.info("[image_thread/service] history deleted | sid=%s", session_id)
        return True
    logger.debug("[image_thread/service] history delete skipped (not found) | sid=%s", session_id)
    return False


def latest_image_path_from_entries(root: str, entries: list[dict]) -> str | None:
    """
    從記憶 entries 末端往前找最新的圖片絕對路徑。

    優先使用 model entry 的 image_path（上輪 LLM 產出），
    其次使用 image entry 的 path（初始圖片）。
    找不到時回傳 None。
    """
    picture_dir = os.path.join(root, "picture")
    for entry in reversed(entries):
        if entry.get("type") == "image":
            rel = entry.get("path", "")
            if rel:
                resolved = os.path.join(picture_dir, rel)
                logger.debug("[image_thread/service] latest image from type=image | rel=%s", rel)
                return resolved
        elif entry.get("type") == "model":
            rel = entry.get("image_path") or ""
            if rel:
                resolved = os.path.join(picture_dir, rel)
                logger.debug("[image_thread/service] latest image from type=model | rel=%s", rel)
                return resolved
    logger.debug("[image_thread/service] latest image not found in entries")
    return None
