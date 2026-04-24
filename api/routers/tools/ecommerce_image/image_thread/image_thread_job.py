"""
Image-thread (child chat) background jobs: decoupled from HTTP, replayable SSE events, JSON per session.
Same pattern as ecommerce_image run_job.
"""
from __future__ import annotations

import asyncio
import io
import json
import os
import threading
import time
from typing import Any

from fastapi import HTTPException

from core.app_logging import get_backend_logger, log_context
from api.deps import readable_error, safe_filename_part
from api.deps import log_extra
from api.routers.tools.ecommerce_image.image_thread.service import (
    latest_image_path_from_entries,
    latest_provider_state_from_entries,
    load_image_thread_history,
    save_image_thread_history,
)
from api.routers.tools.ecommerce_image.services.style_learning import (
    append_learning_event,
    get_style_prompt_by_id,
)
from core.config import (
    get_image_model,
    get_image_output_size,
    get_image_provider,
    get_openai_api_key,
    get_openai_base_url,
    sync_managed_env_from_dotenv,
)
from core.token_logger import log_token_usage

MAX_IMAGE_THREAD_JOB_EVENTS = 500
logger = get_backend_logger("image_thread.job")

_image_thread_jobs: dict[str, "ImageThreadJob"] = {}
_image_thread_registry_lock = asyncio.Lock()
_disk_lock = threading.Lock()


class ImageThreadCancelled(Exception):
    """Image-thread 同步階段偵測到取消請求。"""


def image_thread_job_json_path(root: str, sid: str) -> str:
    return os.path.join(root, "data", f"image_thread_job_{sid}.json")


def _atomic_write_json(path: str, data: dict[str, Any]) -> None:
    d = os.path.dirname(path)
    os.makedirs(d, exist_ok=True)
    temp_path = f"{path}.tmp"
    with open(temp_path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False)
        f.flush()
        os.fsync(f.fileno())
    for attempt in range(5):
        try:
            os.replace(temp_path, path)
            return
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


def read_image_thread_job_disk(root: str, sid: str) -> dict[str, Any] | None:
    path = image_thread_job_json_path(root, sid)
    if not os.path.isfile(path):
        return None
    with _disk_lock:
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
            return data if isinstance(data, dict) else None
        except Exception as exc:
            logger.warning("[image_thread_job] read disk failed | sid=%s err=%s", sid, exc)
            return None


def delete_image_thread_job_file(root: str, sid: str) -> bool:
    path = image_thread_job_json_path(root, sid)
    with _disk_lock:
        if os.path.isfile(path):
            try:
                os.remove(path)
                return True
            except OSError:
                return False
    return False


def _persist_snapshot(root: str, sid: str, snapshot: dict[str, Any]) -> None:
    path = image_thread_job_json_path(root, sid)
    with _disk_lock:
        _atomic_write_json(path, snapshot)


def repair_stale_image_thread_running_on_disk(root: str, sid: str) -> None:
    data = read_image_thread_job_disk(root, sid)
    if not data or data.get("status") != "running":
        return
    logger.warning("[image_thread_job] repair stale running snapshot | sid=%s", sid)
    ev = list(data.get("events") or [])
    ev.append(
        {
            "type": "error",
            "detail": "前次執行已中斷（伺服器重啟），請重新送出。",
        }
    )
    data["status"] = "error"
    data["updated_at"] = time.time()
    data["events"] = ev[-MAX_IMAGE_THREAD_JOB_EVENTS:]
    _persist_snapshot(root, sid, data)


def get_image_thread_status_payload(root: str, sid: str) -> dict[str, Any]:
    job = _image_thread_jobs.get(sid)
    if job is not None:
        return {
            "status": job.status,
            "session_id": sid,
            "has_active_task": job.task is not None and not job.task.done(),
            "event_count": len(job.events),
        }
    disk = read_image_thread_job_disk(root, sid)
    if not disk:
        return {"status": "idle", "session_id": sid}
    if disk.get("status") == "running":
        repair_stale_image_thread_running_on_disk(root, sid)
        disk = read_image_thread_job_disk(root, sid) or disk
    events = disk.get("events") or []
    return {
        "status": disk.get("status", "idle"),
        "session_id": sid,
        "has_active_task": False,
        "event_count": len(events) if isinstance(events, list) else 0,
    }


def _norm_user_text(u: str) -> str:
    return (u or "").strip()


def _norm_session_title(t: str) -> str:
    return (t or "").strip()


async def _image_thread_generate_async(
    root: str,
    sid: str,
    user_text: str,
    session_title: str,
    selected_style_profile_id: str | None,
    latest_image_abs: str,
    previous_provider_state: dict | None,
    cancel_event: threading.Event | None = None,
) -> tuple[str, str | None, dict | None, str]:
    from PIL import Image

    if cancel_event and cancel_event.is_set():
        raise ImageThreadCancelled("run cancelled before start")

    sync_managed_env_from_dotenv(os.path.join(root, ".env"))
    model_name = get_image_model()

    style_prompt = get_style_prompt_by_id(
        root=root,
        selected_profile_id=selected_style_profile_id,
    )
    style_instruction = (
        "你是一位專業的電商圖片修改助手。使用者會提供一張商品圖片，並以文字描述希望如何修改。\n"
        "請依照使用者的描述修改圖片，並回傳修改後的圖片。\n"
        "若使用者的描述不夠清楚，可以先以文字詢問補充資訊，同時也提供一個依目前理解修改的版本。\n"
        "回應時請先以簡短文字說明你做了哪些修改，再提供圖片。"
    ) + (style_prompt or "")

    with open(latest_image_abs, "rb") as img_f:
        ref_image_bytes = img_f.read()

    img_prov = get_image_provider()
    if img_prov == "openai":
        api_key = get_openai_api_key()
        if not api_key:
            raise ValueError("未設定 OPENAI_API_KEY，請至設定頁填寫。")
        from core.providers.openai_compat import OpenAIImageProvider
        provider = OpenAIImageProvider(api_key, get_openai_base_url())
    else:
        api_key = os.environ.get("GOOGLE_API_KEY") or ""
        if not api_key:
            raise ValueError("未設定 GOOGLE_API_KEY，請至設定頁填寫。")
        import google.genai as genai_new
        genai_client = genai_new.Client(api_key=api_key)
        from core.providers.gemini import GeminiImageProvider
        provider = GeminiImageProvider(genai_client)

    edit_result = await provider.edit_image(
        model=model_name,
        image_bytes=ref_image_bytes,
        prompt=user_text,
        style_instruction=style_instruction,
        image_size=get_image_output_size(),
        previous_provider_state=previous_provider_state,
    )

    if cancel_event and cancel_event.is_set():
        raise ImageThreadCancelled("run cancelled after model response")

    try:
        log_token_usage(
            model=model_name,
            source="image_thread",
            input_tokens=edit_result.input_tokens,
            output_tokens=edit_result.output_tokens,
        )
    except Exception as _tok_exc:
        logger.warning("[image_thread] log_token_usage failed | %s", _tok_exc)

    saved_filename: str | None = None
    if edit_result.image_bytes:
        if cancel_event and cancel_event.is_set():
            raise ImageThreadCancelled("run cancelled before image save")
        picture_dir = os.path.join(root, "picture")
        os.makedirs(picture_dir, exist_ok=True)
        safe_title = safe_filename_part(session_title)
        filename = f"{safe_title}_{sid}_{int(time.time())}.jpg"
        out_path = os.path.join(picture_dir, filename)
        img = Image.open(io.BytesIO(edit_result.image_bytes))
        img.load()
        resized = img.resize((1000, 1000), Image.LANCZOS)
        if resized.mode != "RGB":
            resized = resized.convert("RGB")
        resized.save(out_path, "JPEG", quality=92)
        saved_filename = filename

    logger.debug(
        "[image_thread_job] generate done | sid=%s text_chars=%d saved_image=%s",
        sid,
        len(edit_result.text),
        saved_filename or "",
    )
    return edit_result.text, saved_filename, edit_result.provider_state, model_name


class ImageThreadJob:
    def __init__(
        self,
        root: str,
        sid: str,
        user_text: str,
        session_title: str,
        selected_style_profile_id: str | None,
        request_id: str | None,
    ) -> None:
        self.root = root
        self.sid = sid
        self.user_text = user_text
        self.session_title = session_title
        self.selected_style_profile_id = selected_style_profile_id
        self.request_id = request_id
        self.status = "running"
        self.events: list[dict[str, Any]] = []
        self._next_seq = 1
        self._subscribers: set[asyncio.Queue] = set()
        self._job_lock = asyncio.Lock()
        self.task: asyncio.Task | None = None
        self.cancel_event = threading.Event()
        self.cancel_requested_at: float | None = None

    def matches_launch_params(
        self,
        user_text: str,
        session_title: str,
        selected_style_profile_id: str | None,
    ) -> bool:
        a = (self.selected_style_profile_id or "") == (selected_style_profile_id or "")
        return (
            _norm_user_text(self.user_text) == _norm_user_text(user_text)
            and _norm_session_title(self.session_title)
            == _norm_session_title(session_title)
            and a
        )

    def _snapshot(self) -> dict[str, Any]:
        ev = self.events
        if len(ev) > MAX_IMAGE_THREAD_JOB_EVENTS:
            ev = ev[-MAX_IMAGE_THREAD_JOB_EVENTS:]
        return {
            "session_id": self.sid,
            "status": self.status,
            "updated_at": time.time(),
            "started_user_text": self.user_text,
            "session_title": self.session_title,
            "selected_style_profile_id": self.selected_style_profile_id,
            "cancel_requested": self.cancel_event.is_set(),
            "cancel_requested_at": self.cancel_requested_at,
            "last_seq": self._next_seq - 1,
            "events": list(ev),
        }

    async def on_event(self, payload: dict[str, Any]) -> None:
        ev = dict(payload)
        seq = ev.get("seq")
        if not isinstance(seq, int) or seq <= 0:
            seq = self._next_seq
            ev["seq"] = seq
        self._next_seq = max(self._next_seq, seq + 1)
        async with self._job_lock:
            if len(self.events) >= MAX_IMAGE_THREAD_JOB_EVENTS:
                self.events = self.events[-(MAX_IMAGE_THREAD_JOB_EVENTS - 1) :]
            self.events.append(ev)
            snap = self._snapshot()
            queues = list(self._subscribers)
        await asyncio.to_thread(_persist_snapshot, self.root, self.sid, snap)
        logger.debug(
            "[image_thread_job] event appended | sid=%s type=%s seq=%s",
            self.sid,
            ev.get("type"),
            ev.get("seq"),
            extra=log_extra(self.sid, self.request_id),
        )
        for q in queues:
            try:
                q.put_nowait(ev)
            except asyncio.QueueFull:
                pass

    async def register_subscriber(
        self, from_seq: int = 0
    ) -> tuple[list[dict[str, Any]], asyncio.Queue]:
        q: asyncio.Queue = asyncio.Queue(maxsize=500)
        async with self._job_lock:
            if from_seq > 0:
                replay = [
                    e
                    for e in self.events
                    if not isinstance(e.get("seq"), int) or e.get("seq", 0) > from_seq
                ]
            else:
                replay = list(self.events)
            self._subscribers.add(q)
        return replay, q

    async def unregister_subscriber(self, q: asyncio.Queue) -> None:
        async with self._job_lock:
            self._subscribers.discard(q)

    async def _execute(self) -> None:
        with log_context(self.sid, self.request_id):
            logger.info(
                "[image_thread_job] execute start | sid=%s title=%s style_profile=%s",
                self.sid,
                self.session_title,
                self.selected_style_profile_id or "(default)",
            )
            try:
                entries = load_image_thread_history(self.root, self.sid)
                latest_image_abs = latest_image_path_from_entries(self.root, entries)
                if not latest_image_abs or not os.path.exists(latest_image_abs):
                    self.status = "error"
                    logger.warning("[image_thread_job] latest image missing | sid=%s", self.sid)
                    await self.on_event(
                        {
                            "type": "error",
                            "detail": "找不到可用的參考圖片，請確認討論串已正確初始化。",
                        }
                    )
                    return

                await self.on_event({"type": "progress", "content": "正在處理圖片，請稍候…"})

                current_entries = load_image_thread_history(self.root, self.sid)
                prev_state = latest_provider_state_from_entries(current_entries)

                result_text, saved_filename, new_provider_state, model_name = await _image_thread_generate_async(
                    self.root,
                    self.sid,
                    self.user_text,
                    self.session_title,
                    self.selected_style_profile_id,
                    latest_image_abs,
                    prev_state,
                    self.cancel_event,
                )

                current_entries = load_image_thread_history(self.root, self.sid)
                current_entries.append({"type": "user", "text": self.user_text})
                model_entry: dict = {
                    "type": "model",
                    "text": result_text or "",
                    "image_path": saved_filename,
                }
                if new_provider_state:
                    model_entry["provider_state"] = new_provider_state
                current_entries.append(model_entry)
                save_image_thread_history(self.root, self.sid, current_entries)
                try:
                    append_learning_event(
                        root=self.root,
                        session_id=self.sid,
                        user_text=self.user_text,
                        model_text=result_text or "",
                        image_path=saved_filename,
                    )
                except Exception as exc:
                    logger.warning(
                        "[image_thread_job] append learning event failed | sid=%s err=%s",
                        self.sid,
                        exc,
                    )

                self.status = "completed"
                logger.info(
                    "[image_thread_job] execute completed | sid=%s saved_image=%s",
                    self.sid,
                    saved_filename or "",
                )
                await self.on_event(
                    {
                        "type": "complete",
                        "text": result_text or "",
                        "saved_image": saved_filename,
                        "model": model_name,
                    }
                )
            except asyncio.CancelledError:
                self.status = "cancelled"
                logger.warning("[image_thread_job] execute cancelled | sid=%s", self.sid)
                await self.on_event({"type": "error", "detail": "已取消目前流程。"})
                raise
            except ImageThreadCancelled:
                self.status = "cancelled"
                logger.warning("[image_thread_job] execute cancelled (sync stage) | sid=%s", self.sid)
                await self.on_event({"type": "error", "detail": "已取消目前流程。"})
            except Exception as exc:
                self.status = "error"
                logger.error("[image_thread_job] execute failed | sid=%s err=%s", self.sid, exc)
                await self.on_event({"type": "error", "detail": readable_error(exc)})
            finally:
                async with _image_thread_registry_lock:
                    _image_thread_jobs.pop(self.sid, None)
                logger.info("[image_thread_job] execute end | sid=%s status=%s", self.sid, self.status)


async def cancel_image_thread_run(root: str, sid: str) -> bool:
    async with _image_thread_registry_lock:
        job = _image_thread_jobs.get(sid)
    if not job or not job.task or job.task.done():
        logger.info("[image_thread_job] cancel skipped | sid=%s", sid, extra=log_extra(sid, None))
        return False
    now_ts = time.time()
    job.cancel_requested_at = now_ts
    job.cancel_event.set()
    logger.info(
        "[image_thread_job] cancel requested | sid=%s requested_at=%.3f",
        sid,
        now_ts,
        extra=log_extra(sid, None),
    )
    await asyncio.to_thread(_persist_snapshot, root, sid, job._snapshot())
    job.task.cancel()
    try:
        await job.task
    except asyncio.CancelledError:
        pass
    except Exception:
        pass
    return True


async def subscribe_image_thread_events(root: str, sid: str, from_seq: int = 0):
    logger.debug("[image_thread_job] subscribe start | sid=%s from_seq=%d", sid, from_seq, extra=log_extra(sid, None))
    async with _image_thread_registry_lock:
        job = _image_thread_jobs.get(sid)
    if job:
        replay, q = await job.register_subscriber(from_seq=from_seq)
        try:
            for ev in replay:
                yield ev
                if ev.get("type") in ("complete", "error"):
                    return
            while True:
                ev = await q.get()
                yield ev
                if ev.get("type") in ("complete", "error"):
                    return
        finally:
            await job.unregister_subscriber(q)
        return

    data = read_image_thread_job_disk(root, sid)
    if not data:
        logger.warning("[image_thread_job] subscribe no record | sid=%s", sid, extra=log_extra(sid, None))
        yield {"type": "error", "detail": "此 session 沒有可訂閱的執行紀錄。"}
        return
    if data.get("status") == "running":
        repair_stale_image_thread_running_on_disk(root, sid)
        data = read_image_thread_job_disk(root, sid) or data
    events = data.get("events") or []
    if not isinstance(events, list):
        yield {"type": "error", "detail": "執行紀錄格式異常。"}
        return
    for ev in events:
        if isinstance(ev, dict):
            if from_seq > 0:
                seq = ev.get("seq")
                if isinstance(seq, int) and seq <= from_seq:
                    continue
            yield ev
    last = events[-1] if events else None
    if not isinstance(last, dict) or last.get("type") not in ("complete", "error"):
        yield {"type": "error", "detail": "此 session 沒有進行中的任務。"}


async def precheck_and_spawn_image_thread(
    root: str,
    sid: str,
    user_text: str,
    session_title: str,
    selected_style_profile_id: str | None,
    request_id: str | None = None,
) -> None:
    async with _image_thread_registry_lock:
        existing = _image_thread_jobs.get(sid)
        if existing:
            if not existing.matches_launch_params(
                user_text, session_title, selected_style_profile_id
            ):
                raise HTTPException(
                    status_code=409,
                    detail="此對話已有任務執行中，請等待完成或先停止。",
                )
            logger.info(
                "[image_thread_job] reuse existing running job | sid=%s",
                sid,
                extra=log_extra(sid, existing.request_id or request_id),
            )
            return
        new_job = ImageThreadJob(
            root=root,
            sid=sid,
            user_text=user_text,
            session_title=session_title,
            selected_style_profile_id=selected_style_profile_id,
            request_id=request_id,
        )
        initial = new_job._snapshot()
        await asyncio.to_thread(_persist_snapshot, root, sid, initial)
        _image_thread_jobs[sid] = new_job
        new_job.task = asyncio.create_task(
            new_job._execute(), name=f"image_thread_{sid}"
        )
    logger.info("[image_thread_job] spawned new job | sid=%s", sid, extra=log_extra(sid, request_id))
