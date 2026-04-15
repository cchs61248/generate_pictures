import json
import os
import re
import threading
import time
from datetime import datetime, timezone

from core.config import get_text_model
from core.token_logger import log_token_usage
from google.genai import types as genai_types

TOOL_ID = "ecommerce-image"
QUEUE_FILE = "style_learning_queue_ecommerce_image.json"
PROFILE_FILE = "style_profile_ecommerce_image.json"
HISTORY_FILE = "style_profile_ecommerce_image_versions.json"
DEFAULT_PAGE_SIZE = 10
MAX_PAGE_SIZE = 50
MAX_PROFILE_HISTORY = 5
MAX_EVENT_CHARS = 2000
MAX_PROFILE_PROMPT_CHARS = 1400

_lock = threading.Lock()


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _data_dir(root: str) -> str:
    path = os.path.join(root, "data")
    os.makedirs(path, exist_ok=True)
    return path


def _queue_path(root: str) -> str:
    return os.path.join(_data_dir(root), QUEUE_FILE)


def _profile_path(root: str) -> str:
    return os.path.join(_data_dir(root), PROFILE_FILE)


def _history_path(root: str) -> str:
    return os.path.join(_data_dir(root), HISTORY_FILE)


def _read_json(path: str, default):
    if not os.path.exists(path):
        return default
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data
    except Exception:
        return default


def _write_json_atomic(path: str, data) -> None:
    temp = f"{path}.tmp"
    with open(temp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    os.replace(temp, path)


def _normalize_text(raw: str | None) -> str:
    if not raw:
        return ""
    text = re.sub(r"\s+", " ", str(raw)).strip()
    if len(text) > MAX_EVENT_CHARS:
        return text[:MAX_EVENT_CHARS]
    return text


def _default_profile_doc() -> dict:
    return {
        "version": 0,
        "updated_at": "",
        "default_profile_id": "none",
        "profiles": [],
    }


def load_style_profile(root: str) -> dict:
    with _lock:
        data = _read_json(_profile_path(root), _default_profile_doc())
    if not isinstance(data, dict):
        return _default_profile_doc()
    if "profiles" not in data or not isinstance(data["profiles"], list):
        data["profiles"] = []
    if "default_profile_id" not in data:
        data["default_profile_id"] = "none"
    if "version" not in data:
        data["version"] = 0
    if "updated_at" not in data:
        data["updated_at"] = ""
    return data


def _normalize_queue_item(item: dict) -> dict:
    out = dict(item)
    status = str(out.get("status", "pending") or "pending").strip().lower()
    if status not in {"pending", "extracted"}:
        status = "pending"
    out["status"] = status
    out["extracted_version"] = out.get("extracted_version")
    out["extracted_at"] = out.get("extracted_at")
    return out


def list_queue_page(
    root: str,
    page: int = 1,
    page_size: int = DEFAULT_PAGE_SIZE,
    scope: str = "pending",
) -> dict:
    safe_page = max(1, int(page or 1))
    safe_size = max(1, min(MAX_PAGE_SIZE, int(page_size or DEFAULT_PAGE_SIZE)))
    safe_scope = str(scope or "pending").strip().lower()
    if safe_scope not in {"pending", "extracted", "all"}:
        safe_scope = "pending"
    with _lock:
        queue = _read_json(_queue_path(root), [])
    if not isinstance(queue, list):
        queue = []
    normalized = [_normalize_queue_item(i) for i in queue]
    if safe_scope == "all":
        filtered = normalized
    else:
        filtered = [i for i in normalized if i.get("status") == safe_scope]
    filtered.sort(key=lambda x: str(x.get("timestamp", "")), reverse=True)
    total = len(filtered)
    page_count = max(1, (total + safe_size - 1) // safe_size)
    if safe_page > page_count:
        safe_page = page_count
    start = (safe_page - 1) * safe_size
    end = start + safe_size
    return {
        "items": filtered[start:end],
        "page": safe_page,
        "page_size": safe_size,
        "total": total,
        "total_pages": page_count,
        "scope": safe_scope,
    }


def append_learning_event(
    root: str,
    session_id: str,
    user_text: str,
    model_text: str,
    image_path: str | None = None,
) -> dict:
    event = {
        "event_id": f"evt_{int(time.time() * 1000)}_{os.urandom(4).hex()}",
        "timestamp": _utc_now_iso(),
        "tool_id": TOOL_ID,
        "session_id": session_id,
        "user_text": _normalize_text(user_text),
        "model_text": _normalize_text(model_text),
        "image_path": image_path or None,
        "status": "pending",
        "extracted_version": None,
        "extracted_at": None,
    }
    with _lock:
        path = _queue_path(root)
        queue = _read_json(path, [])
        if not isinstance(queue, list):
            queue = []
        queue.append(event)
        _write_json_atomic(path, queue)
    return event


def delete_queue_events(root: str, event_ids: list[str], actor: str = "manual-ui") -> dict:
    ids = {str(i).strip() for i in event_ids if str(i).strip()}
    if not ids:
        return {"deleted": 0, "remaining": list_queue_page(root, 1, 1, scope="all")["total"]}

    with _lock:
        qpath = _queue_path(root)
        queue = _read_json(qpath, [])
        if not isinstance(queue, list):
            queue = []
        before = len(queue)
        kept = [item for item in queue if str(item.get("event_id", "")).strip() not in ids]
        deleted = before - len(kept)
        if deleted > 0:
            _write_json_atomic(qpath, kept)
            _append_history_locked(
                root,
                {
                    "type": "queue_delete",
                    "timestamp": _utc_now_iso(),
                    "deleted_count": deleted,
                    "remaining_count": len(kept),
                    "actor": actor,
                },
            )
    return {"deleted": deleted, "remaining": len(kept)}


def restore_queue_events(root: str, event_ids: list[str], actor: str = "manual-ui") -> dict:
    ids = {str(i).strip() for i in event_ids if str(i).strip()}
    if not ids:
        pending = list_queue_page(root, 1, 1, scope="pending")["total"]
        extracted = list_queue_page(root, 1, 1, scope="extracted")["total"]
        return {"restored": 0, "pending": pending, "extracted": extracted}

    restored = 0
    with _lock:
        qpath = _queue_path(root)
        queue = _read_json(qpath, [])
        if not isinstance(queue, list):
            queue = []
        next_queue: list[dict] = []
        for item in queue:
            norm = _normalize_queue_item(item)
            if str(norm.get("event_id", "")).strip() in ids and norm.get("status") == "extracted":
                norm["status"] = "pending"
                norm["extracted_version"] = None
                norm["extracted_at"] = None
                restored += 1
            next_queue.append(norm)

        if restored > 0:
            _write_json_atomic(qpath, next_queue)
            _append_history_locked(
                root,
                {
                    "type": "queue_restore",
                    "timestamp": _utc_now_iso(),
                    "restored_count": restored,
                    "actor": actor,
                },
            )

        pending = len([i for i in next_queue if i.get("status") == "pending"])
        extracted = len([i for i in next_queue if i.get("status") == "extracted"])
    return {"restored": restored, "pending": pending, "extracted": extracted}


def _append_history_locked(root: str, entry: dict) -> None:
    hpath = _history_path(root)
    history = _read_json(hpath, [])
    if not isinstance(history, list):
        history = []
    history.append(entry)
    if len(history) > 300:
        history = history[-300:]
    _write_json_atomic(hpath, history)


def list_history(root: str, page: int = 1, page_size: int = DEFAULT_PAGE_SIZE) -> dict:
    safe_page = max(1, int(page or 1))
    safe_size = max(1, min(MAX_PAGE_SIZE, int(page_size or DEFAULT_PAGE_SIZE)))
    with _lock:
        history = _read_json(_history_path(root), [])
    if not isinstance(history, list):
        history = []
    history = list(reversed(history))
    total = len(history)
    page_count = max(1, (total + safe_size - 1) // safe_size)
    if safe_page > page_count:
        safe_page = page_count
    start = (safe_page - 1) * safe_size
    end = start + safe_size
    return {
        "items": history[start:end],
        "page": safe_page,
        "page_size": safe_size,
        "total": total,
        "total_pages": page_count,
    }


def _safe_json_extract(text: str) -> dict:
    candidate = (text or "").strip()
    if not candidate:
        raise ValueError("empty llm response")
    try:
        data = json.loads(candidate)
        if isinstance(data, dict):
            return data
    except Exception:
        pass
    start = candidate.find("{")
    end = candidate.rfind("}")
    if start >= 0 and end > start:
        maybe = candidate[start : end + 1]
        data = json.loads(maybe)
        if isinstance(data, dict):
            return data
    raise ValueError("llm response is not valid json object")


def _validate_profile_prompt(raw: str) -> str:
    prompt = _normalize_text(raw)
    if not prompt:
        raise ValueError("萃取結果缺少 prompt")
    if len(prompt) > MAX_PROFILE_PROMPT_CHARS:
        prompt = prompt[:MAX_PROFILE_PROMPT_CHARS]
    banned = ["覆蓋基底規則", "忽略基底規則", "取代 system_instruction"]
    lower = prompt.lower()
    if any(b.lower() in lower for b in banned):
        raise ValueError("萃取結果含不安全指令")
    return prompt


def _profile_prompt_block(profile_prompt: str) -> str:
    return (
        "\n\n## 工具級風格偏好（在不違反基底規則前提下）\n"
        "- 以下為從歷史修圖行為學習到的偏好，僅做微調，不得違反原有硬規則：\n"
        f"{profile_prompt}\n"
    )


def get_style_prompt_by_id(root: str, selected_profile_id: str | None) -> str:
    if not selected_profile_id or selected_profile_id == "none":
        return ""
    profile_doc = load_style_profile(root)
    profiles = profile_doc.get("profiles", [])
    if not isinstance(profiles, list):
        return ""
    for p in profiles:
        if str(p.get("id", "")) == selected_profile_id:
            return _profile_prompt_block(str(p.get("prompt", "")))
    return ""


def get_default_style_prompt(root: str) -> str:
    profile_doc = load_style_profile(root)
    selected = str(profile_doc.get("default_profile_id", "none") or "none")
    return get_style_prompt_by_id(root, selected)


def _current_default_profile(profile_doc: dict) -> dict | None:
    default_id = str(profile_doc.get("default_profile_id", "none") or "none")
    if default_id == "none":
        return None
    profiles = profile_doc.get("profiles", [])
    if not isinstance(profiles, list):
        return None
    for p in profiles:
        if str(p.get("id", "")) == default_id:
            return p
    return None


def extract_style_profile(
    root: str,
    genai_client,
    base_style_prompt: str,
    actor: str = "manual-ui",
) -> dict:
    with _lock:
        qpath = _queue_path(root)
        ppath = _profile_path(root)
        queue = _read_json(qpath, [])
        if not isinstance(queue, list):
            queue = []
        normalized_queue = [_normalize_queue_item(i) for i in queue]
        pending_items = [i for i in normalized_queue if i.get("status") == "pending"]
        queue_before = len(pending_items)
        if queue_before == 0:
            return {
                "ok": False,
                "reason": "pending queue is empty",
                "queue_before": 0,
                "queue_after": 0,
            }
        samples = pending_items[:200]
        profile_doc = _read_json(ppath, _default_profile_doc())
        if not isinstance(profile_doc, dict):
            profile_doc = _default_profile_doc()
        profiles = profile_doc.get("profiles", [])
        if not isinstance(profiles, list):
            profiles = []
        if len(profiles) >= MAX_PROFILE_HISTORY:
            return {
                "ok": False,
                "reason": "profile_limit_reached",
                "profile_limit": MAX_PROFILE_HISTORY,
                "profile_count": len(profiles),
                "queue_before": queue_before,
                "queue_after": queue_before,
            }
        active_profile = _current_default_profile(profile_doc)

    lines: list[str] = []
    for idx, evt in enumerate(samples, 1):
        user_text = _normalize_text(str(evt.get("user_text", "")))
        model_text = _normalize_text(str(evt.get("model_text", "")))
        if not user_text and not model_text:
            continue
        lines.append(f"[{idx}] user: {user_text}")
        lines.append(f"[{idx}] model: {model_text}")
    if not lines:
        return {
            "ok": False,
            "reason": "queue has no usable text",
            "queue_before": queue_before,
            "queue_after": queue_before,
        }
    training_text = "\n".join(lines)
    current_profile_text = "（目前未設定工具級風格偏好）"
    current_profile_meta = "none"
    if active_profile:
        current_profile_meta = str(active_profile.get("id", "none") or "none")
        current_profile_text = _normalize_text(str(active_profile.get("prompt", "")))

    prompt = f"""
你是「AI 電商圖文助手」的風格學習器。請根據歷史修圖對話，產生一段「可附加在系統提示詞後方」的工具級偏好提示。

限制：
1) 不可覆蓋或否定基底硬規則。
2) 只能輸出 JSON 物件，不要 markdown，不要解釋文字。
3) JSON 格式：
{{
  "name": "12字內偏好名稱",
  "summary": "40字內摘要",
  "prompt": "可直接附加的偏好指令（繁中）"
}}

基底硬規則如下（不可被覆蓋）：
{base_style_prompt}

目前正在使用的工具級風格偏好（請視為既有版本，做增量修正而非重寫）：
{current_profile_text}

歷史修圖對話：
{training_text}
""".strip()

    response = genai_client.models.generate_content(
        model=get_text_model(),
        contents=[prompt],
        config=genai_types.GenerateContentConfig(
            response_mime_type="application/json",
            temperature=0.2,
        ),
    )
    usage = getattr(response, "usage_metadata", None)
    if usage is not None:
        try:
            log_token_usage(
                model=get_text_model(),
                source="style_learning_extract",
                input_tokens=getattr(usage, "prompt_token_count", 0) or 0,
                output_tokens=getattr(usage, "candidates_token_count", 0) or 0,
            )
        except Exception:
            pass
    text = getattr(response, "text", "") or ""
    parsed = _safe_json_extract(text)
    prof_name = _normalize_text(str(parsed.get("name", ""))) or "自動萃取偏好"
    prof_summary = _normalize_text(str(parsed.get("summary", "")))
    prof_prompt = _validate_profile_prompt(str(parsed.get("prompt", "")))

    with _lock:
        ppath = _profile_path(root)
        hpath = _history_path(root)
        qpath = _queue_path(root)

        profile_doc = _read_json(ppath, _default_profile_doc())
        if not isinstance(profile_doc, dict):
            profile_doc = _default_profile_doc()
        profiles = profile_doc.get("profiles", [])
        if not isinstance(profiles, list):
            profiles = []
        version = int(profile_doc.get("version", 0) or 0) + 1
        new_id = f"profile_{version}_{int(time.time())}"
        new_profile = {
            "id": new_id,
            "name": prof_name[:24],
            "summary": prof_summary[:64],
            "prompt": prof_prompt,
            "created_at": _utc_now_iso(),
            "source_event_count": len(samples),
            "version": version,
        }
        profiles.append(new_profile)
        if len(profiles) > MAX_PROFILE_HISTORY:
            profiles = profiles[-MAX_PROFILE_HISTORY:]

        profile_doc = {
            "version": version,
            "updated_at": _utc_now_iso(),
            "default_profile_id": new_id,
            "profiles": profiles,
        }
        sample_ids = {str(i.get("event_id", "")) for i in samples}
        next_queue: list[dict] = []
        for item in normalized_queue:
            norm = _normalize_queue_item(item)
            if str(norm.get("event_id", "")) in sample_ids and norm.get("status") == "pending":
                norm["status"] = "extracted"
                norm["extracted_version"] = version
                norm["extracted_at"] = _utc_now_iso()
            next_queue.append(norm)
        _write_json_atomic(ppath, profile_doc)
        _write_json_atomic(qpath, next_queue)

        history = _read_json(hpath, [])
        if not isinstance(history, list):
            history = []
        history.append(
            {
                "type": "extract",
                "timestamp": _utc_now_iso(),
                "version": version,
                "profile_id": new_id,
                "profile_name": new_profile["name"],
                "based_on_profile_id": current_profile_meta,
                "source_event_count": len(samples),
                "queue_before": queue_before,
                "queue_after": max(0, queue_before - len(samples)),
                "actor": actor,
            }
        )
        if len(history) > 300:
            history = history[-300:]
        _write_json_atomic(hpath, history)

    return {
        "ok": True,
        "profile": new_profile,
        "queue_before": queue_before,
        "queue_after": max(0, queue_before - len(samples)),
    }


def rename_profile(
    root: str,
    profile_id: str,
    new_name: str,
    actor: str = "manual-ui",
) -> dict:
    pid = str(profile_id or "").strip()
    name = _normalize_text(new_name)
    if not pid:
        raise ValueError("profile_id is required")
    if not name:
        raise ValueError("new_name is required")

    with _lock:
        ppath = _profile_path(root)
        profile_doc = _read_json(ppath, _default_profile_doc())
        if not isinstance(profile_doc, dict):
            profile_doc = _default_profile_doc()
        profiles = profile_doc.get("profiles", [])
        if not isinstance(profiles, list):
            profiles = []

        found = None
        for p in profiles:
            if str(p.get("id", "")) == pid:
                found = p
                break
        if not found:
            raise ValueError("profile_id not found")

        old_name = str(found.get("name", "") or "")
        found["name"] = name[:24]
        profile_doc["updated_at"] = _utc_now_iso()
        _write_json_atomic(ppath, profile_doc)
        _append_history_locked(
            root,
            {
                "type": "profile_rename",
                "timestamp": _utc_now_iso(),
                "profile_id": pid,
                "old_name": old_name,
                "new_name": found["name"],
                "actor": actor,
            },
        )
    return {"ok": True, "profile_id": pid, "name": found["name"]}


def rollback_profile(root: str, profile_id: str, actor: str = "manual-ui") -> dict:
    pid = str(profile_id or "").strip()
    if not pid:
        raise ValueError("profile_id is required")
    with _lock:
        ppath = _profile_path(root)
        hpath = _history_path(root)
        profile_doc = _read_json(ppath, _default_profile_doc())
        if not isinstance(profile_doc, dict):
            profile_doc = _default_profile_doc()
        profiles = profile_doc.get("profiles", [])
        if not isinstance(profiles, list):
            profiles = []
        found = None
        for p in profiles:
            if str(p.get("id", "")) == pid:
                found = p
                break
        if not found:
            raise ValueError("profile_id not found")

        profile_doc["default_profile_id"] = pid
        profile_doc["updated_at"] = _utc_now_iso()
        _write_json_atomic(ppath, profile_doc)

        _append_history_locked(
            root,
            {
                "type": "rollback",
                "timestamp": _utc_now_iso(),
                "to_profile_id": pid,
                "to_profile_name": str(found.get("name", "")),
                "actor": actor,
            },
        )
    return {"ok": True, "default_profile_id": pid}


def delete_profile(root: str, profile_id: str, actor: str = "manual-ui") -> dict:
    pid = str(profile_id or "").strip()
    if not pid:
        raise ValueError("profile_id is required")

    with _lock:
        ppath = _profile_path(root)
        profile_doc = _read_json(ppath, _default_profile_doc())
        if not isinstance(profile_doc, dict):
            profile_doc = _default_profile_doc()
        profiles = profile_doc.get("profiles", [])
        if not isinstance(profiles, list):
            profiles = []

        removed = None
        kept: list[dict] = []
        for p in profiles:
            if str(p.get("id", "")) == pid:
                removed = p
                continue
            kept.append(p)
        if removed is None:
            raise ValueError("profile_id not found")

        current_default = str(profile_doc.get("default_profile_id", "none") or "none")
        next_default = current_default
        if current_default == pid:
            next_default = str(kept[-1].get("id", "none")) if kept else "none"

        profile_doc["profiles"] = kept
        profile_doc["default_profile_id"] = next_default
        profile_doc["updated_at"] = _utc_now_iso()
        _write_json_atomic(ppath, profile_doc)
        _append_history_locked(
            root,
            {
                "type": "profile_delete",
                "timestamp": _utc_now_iso(),
                "deleted_profile_id": pid,
                "deleted_profile_name": str(removed.get("name", "")),
                "default_before": current_default,
                "default_after": next_default,
                "actor": actor,
            },
        )

    return {
        "ok": True,
        "deleted_profile_id": pid,
        "default_profile_id": next_default,
    }
