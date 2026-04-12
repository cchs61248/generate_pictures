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


def _history_path(root: str, session_id: str) -> str:
    data_dir = os.path.join(root, "data")
    os.makedirs(data_dir, exist_ok=True)
    return os.path.join(data_dir, f"image_thread_history_{session_id}.json")


def load_image_thread_history(root: str, session_id: str) -> list[dict]:
    """讀取指定 session 的 image thread 歷史；不存在或損毀時回傳空列表。"""
    path = _history_path(root, session_id)
    if not os.path.exists(path):
        return []
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, list):
            return data
    except Exception:
        pass
    return []


def save_image_thread_history(root: str, session_id: str, entries: list[dict]) -> None:
    """將 image thread 歷史寫入磁碟（atomic write）。"""
    path = _history_path(root, session_id)
    temp_path = f"{path}.tmp"
    with open(temp_path, "w", encoding="utf-8") as f:
        json.dump(entries, f, ensure_ascii=False)
    os.replace(temp_path, path)


def delete_image_thread_history(root: str, session_id: str) -> bool:
    """刪除指定 session 的 image thread 歷史；不存在時回傳 False。"""
    path = _history_path(root, session_id)
    if os.path.exists(path):
        os.remove(path)
        return True
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
                return os.path.join(picture_dir, rel)
        elif entry.get("type") == "model":
            rel = entry.get("image_path") or ""
            if rel:
                return os.path.join(picture_dir, rel)
    return None
