"""
Session 狀態管理 Router

負責：
  - GET/PUT /session-state：讀寫前端 session 狀態（chat 列表、activeId、version 衝突檢查）
  - 狀態持久化至 data/session_state.json
"""
import json
import os
import time

from fastapi import APIRouter, HTTPException

from api.deps import project_root

router = APIRouter(tags=["session"])

SESSION_STATE_FILE = "session_state.json"


# ── 內部工具函式 ──────────────────────────────────────────────────────────────

def _session_state_path(root: str) -> str:
    data_dir = os.path.join(root, "data")
    os.makedirs(data_dir, exist_ok=True)
    return os.path.join(data_dir, SESSION_STATE_FILE)


def load_session_state(root: str) -> dict:
    """從磁碟讀取 session 狀態；檔案不存在或損毀時回傳預設空狀態。"""
    path = _session_state_path(root)
    if not os.path.exists(path):
        return {"sessions": [], "activeId": "", "version": 0, "deletedIds": []}
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        if not isinstance(data, dict):
            return {"sessions": [], "activeId": "", "version": 0, "deletedIds": []}
        sessions = data.get("sessions")
        active_id = data.get("activeId")
        version = data.get("version", 0)
        deleted_ids = data.get("deletedIds", [])
        if (
            not isinstance(sessions, list)
            or not isinstance(active_id, str)
            or not isinstance(version, int)
        ):
            return {"sessions": [], "activeId": "", "version": 0, "deletedIds": []}
        if not isinstance(deleted_ids, list):
            deleted_ids = []
        return {
            "sessions": sessions,
            "activeId": active_id,
            "version": max(version, 0),
            "deletedIds": deleted_ids,
        }
    except Exception:
        return {"sessions": [], "activeId": "", "version": 0, "deletedIds": []}


def save_session_state(root: str, payload: dict) -> dict:
    """
    驗證並寫入 session 狀態至磁碟。

    - 需帶 expectedVersion 做樂觀鎖衝突偵測
    - deletedIds 只累加，不清空
    """
    sessions = payload.get("sessions")
    active_id = payload.get("activeId")
    if not isinstance(sessions, list) or not isinstance(active_id, str):
        raise HTTPException(status_code=400, detail="invalid session state payload")

    expected_version = payload.get("expectedVersion")
    if not isinstance(expected_version, int):
        raise HTTPException(status_code=400, detail="expectedVersion must be integer")

    incoming_deleted = payload.get("deletedIds", [])
    if not isinstance(incoming_deleted, list):
        incoming_deleted = []

    current = load_session_state(root)
    current_version = int(current.get("version", 0))
    if expected_version != current_version:
        raise HTTPException(status_code=409, detail="session state version conflict")

    existing_deleted = current.get("deletedIds", [])
    if not isinstance(existing_deleted, list):
        existing_deleted = []
    merged_deleted = list(dict.fromkeys(existing_deleted + incoming_deleted))

    path = _session_state_path(root)
    temp_path = f"{path}.tmp"
    data = {
        "sessions": sessions,
        "activeId": active_id,
        "version": current_version + 1,
        "deletedIds": merged_deleted,
    }
    with open(temp_path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False)
        f.flush()
        os.fsync(f.fileno())

    # Windows 上目標檔若被短暫占用，os.replace 可能拋 PermissionError。
    # 先做短重試；若仍失敗則退回直接覆寫，避免 API 因暫時鎖檔而 500。
    for attempt in range(5):
        try:
            os.replace(temp_path, path)
            break
        except PermissionError:
            if attempt == 4:
                with open(path, "w", encoding="utf-8") as f:
                    json.dump(data, f, ensure_ascii=False)
                    f.flush()
                    os.fsync(f.fileno())
                try:
                    os.remove(temp_path)
                except OSError:
                    pass
            else:
                time.sleep(0.05 * (attempt + 1))
    return data


# ── Route Handlers ─────────────────────────────────────────────────────────────

@router.get("/session-state")
async def get_session_state():
    """取得目前保存的 session 狀態（前端重啟後可還原）。"""
    return load_session_state(project_root())


@router.put("/session-state")
async def put_session_state(payload: dict):
    """覆寫保存的 session 狀態（含版本衝突檢查）。"""
    saved = save_session_state(project_root(), payload)
    return {"ok": True, **saved}
