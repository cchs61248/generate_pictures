"""
電商主流程 /run-stream 背景任務：與 HTTP 連線解耦、事件可重播、每 session 一個 JSON 持久化。
"""
from __future__ import annotations

import asyncio
import json
import os
import threading
import time
from typing import Any

from fastapi import HTTPException

from api.deps import (
    apply_session_sample_path,
    load_session_documents,
    readable_error,
)
from core.config import parse_config, sync_managed_env_from_dotenv
from api.routers.tools.ecommerce_image.pipeline import run_pipeline

MAX_RUN_JOB_EVENTS = 3000

_jobs: dict[str, "RunJob"] = {}
_registry_lock = asyncio.Lock()
_disk_lock = threading.Lock()


def run_job_json_path(root: str, sid: str) -> str:
    return os.path.join(root, "data", f"run_job_ecommerce_image_{sid}.json")


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


def read_run_job_disk(root: str, sid: str) -> dict[str, Any] | None:
    path = run_job_json_path(root, sid)
    if not os.path.isfile(path):
        return None
    with _disk_lock:
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
            return data if isinstance(data, dict) else None
        except Exception:
            return None


def delete_run_job_file(root: str, sid: str) -> bool:
    path = run_job_json_path(root, sid)
    with _disk_lock:
        if os.path.isfile(path):
            try:
                os.remove(path)
                return True
            except OSError:
                return False
    return False


def _persist_snapshot(root: str, sid: str, snapshot: dict[str, Any]) -> None:
    path = run_job_json_path(root, sid)
    with _disk_lock:
        _atomic_write_json(path, snapshot)


def repair_stale_running_on_disk(root: str, sid: str) -> None:
    """磁碟為 running 但程序內無 Task 時（例如 uvicorn reload），標記為錯誤並寫回。"""
    data = read_run_job_disk(root, sid)
    if not data or data.get("status") != "running":
        return
    ev = list(data.get("events") or [])
    ev.append(
        {
            "type": "error",
            "detail": "前次執行已中斷（伺服器重啟），請重新送出。",
        }
    )
    data["status"] = "error"
    data["updated_at"] = time.time()
    data["events"] = ev[-MAX_RUN_JOB_EVENTS:]
    _persist_snapshot(root, sid, data)


def get_run_status_payload(root: str, sid: str) -> dict[str, Any]:
    """供 GET /run/status：記憶體優先，其次磁碟；會修復 stale running。"""
    job = _jobs.get(sid)
    if job is not None:
        return {
            "status": job.status,
            "session_id": sid,
            "has_active_task": job.task is not None and not job.task.done(),
            "event_count": len(job.events),
        }
    disk = read_run_job_disk(root, sid)
    if not disk:
        return {"status": "idle", "session_id": sid}
    if disk.get("status") == "running":
        repair_stale_running_on_disk(root, sid)
        disk = read_run_job_disk(root, sid) or disk
    events = disk.get("events") or []
    return {
        "status": disk.get("status", "idle"),
        "session_id": sid,
        "has_active_task": False,
        "event_count": len(events) if isinstance(events, list) else 0,
    }


class RunJobProgressSink:
    """duck 相容 ProgressBus（emit / emit_sync），供 stage1 context 與 web_search 使用。"""

    def __init__(self, loop: asyncio.AbstractEventLoop, job: "RunJob") -> None:
        self._loop = loop
        self._job = job

    async def emit(self, payload: dict[str, Any]) -> None:
        await self._job.on_event(payload)

    def emit_sync(self, payload: dict[str, Any]) -> None:
        asyncio.run_coroutine_threadsafe(self._job.on_event(payload), self._loop)


class RunJob:
    def __init__(
        self,
        root: str,
        sid: str,
        user_input: str | None,
        stage3_only: bool,
        selected_style_profile_id: str | None,
    ) -> None:
        self.root = root
        self.sid = sid
        self.user_input = user_input
        self.stage3_only = stage3_only
        self.selected_style_profile_id = selected_style_profile_id
        self.status = "running"
        self.events: list[dict[str, Any]] = []
        self._subscribers: set[asyncio.Queue] = set()
        self._job_lock = asyncio.Lock()
        self.task: asyncio.Task | None = None

    def matches_launch_params(
        self,
        user_input: str | None,
        stage3_only: bool,
        selected_style_profile_id: str | None,
    ) -> bool:
        a = (self.selected_style_profile_id or "") == (selected_style_profile_id or "")
        return (
            _normalize_user_input(self.user_input) == _normalize_user_input(user_input)
            and self.stage3_only == stage3_only
            and a
        )

    def _snapshot(self) -> dict[str, Any]:
        ev = self.events
        if len(ev) > MAX_RUN_JOB_EVENTS:
            ev = ev[-MAX_RUN_JOB_EVENTS:]
        return {
            "session_id": self.sid,
            "status": self.status,
            "updated_at": time.time(),
            "started_user_input": self.user_input,
            "stage3_only": self.stage3_only,
            "selected_style_profile_id": self.selected_style_profile_id,
            "events": list(ev),
        }

    async def on_event(self, payload: dict[str, Any]) -> None:
        async with self._job_lock:
            if len(self.events) >= MAX_RUN_JOB_EVENTS:
                self.events = self.events[-(MAX_RUN_JOB_EVENTS - 1) :]
            self.events.append(payload)
            snap = self._snapshot()
            queues = list(self._subscribers)
        await asyncio.to_thread(_persist_snapshot, self.root, self.sid, snap)
        for q in queues:
            try:
                q.put_nowait(payload)
            except asyncio.QueueFull:
                pass

    async def register_subscriber(self) -> tuple[list[dict[str, Any]], asyncio.Queue]:
        q: asyncio.Queue = asyncio.Queue(maxsize=500)
        async with self._job_lock:
            replay = list(self.events)
            self._subscribers.add(q)
        return replay, q

    async def unregister_subscriber(self, q: asyncio.Queue) -> None:
        async with self._job_lock:
            self._subscribers.discard(q)

    async def _execute_pipeline(self) -> None:
        try:
            sync_managed_env_from_dotenv(os.path.join(self.root, ".env"))
            config = parse_config(stage3_only_flag=self.stage3_only)
            config = apply_session_sample_path(config, self.sid)
            docs = load_session_documents(self.root, config.session_id or self.sid)
            doc_texts = [d["text"] for d in docs]
            doc_filenames = [d["filename"] for d in docs]
            loop = asyncio.get_running_loop()
            sink = RunJobProgressSink(loop, self)
            result = await run_pipeline(
                config=config,
                user_input=self.user_input,
                doc_texts=doc_texts,
                doc_filenames=doc_filenames,
                progress=sink,
                selected_style_profile_id=self.selected_style_profile_id,
            )
            self.status = "completed"
            await self.on_event(
                {
                    "type": "complete",
                    "saved_files": result["saved_files"],
                    "final_output_path": result["final_output_path"],
                }
            )
        except asyncio.CancelledError:
            self.status = "cancelled"
            await self.on_event({"type": "error", "detail": "已取消目前流程。"})
            raise
        except Exception as exc:
            self.status = "error"
            await self.on_event({"type": "error", "detail": readable_error(exc)})
        finally:
            async with _registry_lock:
                _jobs.pop(self.sid, None)


def _normalize_user_input(u: str | None) -> str:
    return (u or "").strip()


async def cancel_ecommerce_run(root: str, sid: str) -> bool:
    """取消進行中任務；若無進行中則回 False。"""
    async with _registry_lock:
        job = _jobs.get(sid)
    if not job or not job.task or job.task.done():
        return False
    job.task.cancel()
    try:
        await job.task
    except asyncio.CancelledError:
        pass
    except Exception:
        pass
    return True


async def subscribe_session_events(root: str, sid: str):
    """重播 + 即時事件；斷線時由呼叫端停止迭代，不取消背景 Task。"""
    async with _registry_lock:
        job = _jobs.get(sid)
    if job:
        replay, q = await job.register_subscriber()
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

    data = read_run_job_disk(root, sid)
    if not data:
        yield {"type": "error", "detail": "此 session 沒有可訂閱的執行紀錄。"}
        return
    if data.get("status") == "running":
        repair_stale_running_on_disk(root, sid)
        data = read_run_job_disk(root, sid) or data
    events = data.get("events") or []
    if not isinstance(events, list):
        yield {"type": "error", "detail": "執行紀錄格式異常。"}
        return
    for ev in events:
        if isinstance(ev, dict):
            yield ev
    last = events[-1] if events else None
    if not isinstance(last, dict) or last.get("type") not in ("complete", "error"):
        yield {"type": "error", "detail": "此 session 沒有進行中的任務。"}


async def precheck_and_spawn_run(
    root: str,
    sid: str,
    user_input: str | None,
    stage3_only: bool,
    selected_style_profile_id: str | None,
) -> None:
    """
    於回傳 StreamingResponse 前呼叫（單一 lock）：若已有任務且參數不同則 409；
    否則必要時建立背景 Task。
    """
    async with _registry_lock:
        existing = _jobs.get(sid)
        if existing:
            if not existing.matches_launch_params(
                user_input, stage3_only, selected_style_profile_id
            ):
                raise HTTPException(
                    status_code=409,
                    detail="此對話已有任務執行中，請等待完成或先停止。",
                )
            return
        new_job = RunJob(
            root=root,
            sid=sid,
            user_input=user_input,
            stage3_only=stage3_only,
            selected_style_profile_id=selected_style_profile_id,
        )
        initial = new_job._snapshot()
        await asyncio.to_thread(_persist_snapshot, root, sid, initial)
        _jobs[sid] = new_job
        new_job.task = asyncio.create_task(
            new_job._execute_pipeline(), name=f"ecommerce_run_{sid}"
        )
