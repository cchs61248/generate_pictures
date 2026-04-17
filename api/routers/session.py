"""
Session 狀態管理 Router

負責：
  - GET/PUT /session-state：讀寫前端 session 狀態（chat 列表、activeId、version 衝突檢查）
  - 狀態持久化至 data/session_state.json
"""
import json
import os
import time
import threading

from fastapi import APIRouter, HTTPException

from core.app_logging import get_backend_logger
from api.deps import project_root

router = APIRouter(tags=["session"])
logger = get_backend_logger("session.router")

SESSION_STATE_FILE = "session_state.json"
_conflict_warn_lock = threading.Lock()
_conflict_warn_at: dict[str, float] = {}
_CONFLICT_WARN_WINDOW_SECONDS = 5.0


def _should_warn_conflict(expected_version: int, current_version: int) -> bool:
    key = f"{expected_version}->{current_version}"
    now = time.time()
    with _conflict_warn_lock:
        last = _conflict_warn_at.get(key, 0.0)
        if now - last < _CONFLICT_WARN_WINDOW_SECONDS:
            return False
        _conflict_warn_at[key] = now
        # Avoid unbounded growth.
        if len(_conflict_warn_at) > 256:
            stale_before = now - (_CONFLICT_WARN_WINDOW_SECONDS * 4)
            for k in list(_conflict_warn_at.keys()):
                if _conflict_warn_at[k] < stale_before:
                    _conflict_warn_at.pop(k, None)
        return True


# ── 內部工具函式 ──────────────────────────────────────────────────────────────

def _session_state_path(root: str) -> str:
    data_dir = os.path.join(root, "data")
    os.makedirs(data_dir, exist_ok=True)
    return os.path.join(data_dir, SESSION_STATE_FILE)


def load_session_state(root: str) -> dict:
    """從磁碟讀取 session 狀態；檔案不存在或損毀時回傳預設空狀態。"""
    path = _session_state_path(root)
    if not os.path.exists(path):
        logger.debug("[session] state file not found; return default")
        return {"sessions": [], "activeId": "", "version": 0, "deletedIds": []}
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        if not isinstance(data, dict):
            logger.warning("[session] invalid state data type; reset to default")
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
            logger.warning("[session] invalid state structure; reset to default")
            return {"sessions": [], "activeId": "", "version": 0, "deletedIds": []}
        if not isinstance(deleted_ids, list):
            deleted_ids = []
        return {
            "sessions": sessions,
            "activeId": active_id,
            "version": max(version, 0),
            "deletedIds": deleted_ids,
        }
    except Exception as exc:
        logger.warning("[session] state load failed; reset to default | err=%s", exc)
        return {"sessions": [], "activeId": "", "version": 0, "deletedIds": []}


def save_session_state(root: str, payload: dict) -> dict:
    """
    驗證並寫入 session 狀態至磁碟。

    - 需帶 expectedVersion 做樂觀鎖衝突偵測
    - deletedIds 只累加，不清空
    """
    sessions = payload.get("sessions")
    active_id = payload.get("activeId")
    logger.debug("[session] save request received")
    if not isinstance(sessions, list) or not isinstance(active_id, str):
        logger.warning("[session] invalid payload")
        raise HTTPException(status_code=400, detail="invalid session state payload")

    expected_version = payload.get("expectedVersion")
    if not isinstance(expected_version, int):
        logger.warning("[session] expectedVersion not integer")
        raise HTTPException(status_code=400, detail="expectedVersion must be integer")

    incoming_deleted = payload.get("deletedIds", [])
    if not isinstance(incoming_deleted, list):
        incoming_deleted = []

    current = load_session_state(root)
    current_version = int(current.get("version", 0))
    if expected_version != current_version:
        if _should_warn_conflict(expected_version, current_version):
            logger.warning(
                "[session] version conflict | expected=%d current=%d",
                expected_version,
                current_version,
            )
        else:
            logger.debug(
                "[session] version conflict (suppressed) | expected=%d current=%d",
                expected_version,
                current_version,
            )
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
            logger.warning("[session] os.replace permission error | attempt=%d", attempt + 1)
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
    logger.debug(
        "[session] save done | sessions=%d activeId=%s version=%d deletedIds=%d",
        len(sessions),
        active_id,
        data["version"],
        len(merged_deleted),
    )
    return data


# ── Route Handlers ─────────────────────────────────────────────────────────────

@router.get("/session-state")
async def get_session_state():
    """取得目前保存的 session 狀態（前端重啟後可還原）。"""
    logger.debug("[session] GET /session-state")
    state = load_session_state(project_root())
    logger.debug(
        "[session] GET done | sessions=%d activeId=%s version=%d",
        len(state.get("sessions", [])),
        state.get("activeId", ""),
        state.get("version", 0),
    )
    return state


@router.put("/session-state")
async def put_session_state(payload: dict):
    """覆寫保存的 session 狀態（含版本衝突檢查）。"""
    logger.debug("[session] PUT /session-state")
    saved = save_session_state(project_root(), payload)
    logger.debug("[session] PUT done | version=%d", saved.get("version", 0))
    return {"ok": True, **saved}
