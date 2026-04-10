import asyncio
import contextlib
import io
import json
import os
import re

from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, StreamingResponse
from PIL import Image

from core.config import (
    DEFAULT_IMAGE_MODEL,
    DEFAULT_TEXT_MODEL,
    ENV_VARS_HIDDEN_FROM_SETTINGS_UI,
    IMAGE_MODEL_OPTIONS,
    MANAGED_ENV_KEYS,
    MANAGED_ENV_VARS,
    TEXT_MODEL_OPTIONS,
    parse_config,
    parse_env_file,
    sync_managed_env_from_dotenv,
    write_managed_env_file,
)
from core.pipeline import run_pipeline
from core.progress import ProgressBus


SESSION_STATE_FILE = "session_state.json"


def _project_root() -> str:
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _safe_session_id(session_id: str | None) -> str | None:
    if not session_id:
        return None
    sid = session_id.strip()
    if not sid:
        return None
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", sid):
        raise HTTPException(status_code=400, detail="invalid session_id")
    return sid


def _sample_image_path_for_session(project_root: str, session_id: str | None) -> str:
    sid = _safe_session_id(session_id)
    if sid:
        uploads_dir = os.path.join(project_root, "uploads")
        os.makedirs(uploads_dir, exist_ok=True)
        return os.path.join(uploads_dir, f"{sid}.jpg")
    return os.path.join(project_root, "sample.jpg")


def _upload_image_path_for_session(project_root: str, session_id: str) -> str:
    sid = _safe_session_id(session_id)
    if not sid:
        raise HTTPException(status_code=400, detail="invalid session_id")
    return os.path.join(project_root, "uploads", f"{sid}.jpg")


def _apply_session_sample_path(config, session_id: str | None):
    project_root = _project_root()
    sid = _safe_session_id(session_id)
    config.sample_image_path = _sample_image_path_for_session(project_root, sid)
    config.session_id = sid or ""
    if sid:
        template_dir = os.path.join(project_root, "template_json")
        config.final_output_path = os.path.join(template_dir, f"final_output_{sid}.json")
    return config


def _readable_error(exc: Exception) -> str:
    detail = str(exc).strip()
    name = exc.__class__.__name__
    if detail:
        if detail == name:
            return detail
        return f"{name}: {detail}"
    return name


def _session_state_path(project_root: str) -> str:
    data_dir = os.path.join(project_root, "data")
    os.makedirs(data_dir, exist_ok=True)
    return os.path.join(data_dir, SESSION_STATE_FILE)


def _load_session_state(project_root: str) -> dict:
    path = _session_state_path(project_root)
    if not os.path.exists(path):
        return {"sessions": [], "activeId": ""}
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        if not isinstance(data, dict):
            return {"sessions": [], "activeId": ""}
        sessions = data.get("sessions")
        active_id = data.get("activeId")
        if not isinstance(sessions, list) or not isinstance(active_id, str):
            return {"sessions": [], "activeId": ""}
        return {"sessions": sessions, "activeId": active_id}
    except Exception:
        return {"sessions": [], "activeId": ""}


def _save_session_state(project_root: str, payload: dict) -> dict:
    sessions = payload.get("sessions")
    active_id = payload.get("activeId")
    if not isinstance(sessions, list) or not isinstance(active_id, str):
        raise HTTPException(status_code=400, detail="invalid session state payload")

    path = _session_state_path(project_root)
    temp_path = f"{path}.tmp"
    data = {"sessions": sessions, "activeId": active_id}
    with open(temp_path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False)
    os.replace(temp_path, path)
    return data


app = FastAPI(title="Generate Pictures API", version="0.1.0")


@app.on_event("startup")
async def startup_load_env():
    project_root = _project_root()
    sync_managed_env_from_dotenv(os.path.join(project_root, ".env"))


app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/session-state")
async def get_session_state():
    """取得目前保存的 session 狀態（前端重啟後可還原）。"""
    project_root = _project_root()
    return _load_session_state(project_root)


@app.get("/settings/env")
async def get_env_settings():
    """回傳受管理環境變數的說明與目前值（來自專案 .env）；隱藏鍵不列出，供設定頁使用。"""
    project_root = _project_root()
    env_path = os.path.join(project_root, ".env")
    parsed = parse_env_file(env_path)
    variables = []
    for spec in MANAGED_ENV_VARS:
        if spec.key in ENV_VARS_HIDDEN_FROM_SETTINGS_UI:
            continue
        val = (parsed.get(spec.key, "") or "").strip()
        if spec.key == "TEXT_MODEL":
            val = val or DEFAULT_TEXT_MODEL
        elif spec.key == "IMAGE_MODEL":
            val = val or DEFAULT_IMAGE_MODEL
        variables.append(
            {
                "key": spec.key,
                "description": spec.description,
                "value": val,
            }
        )
    model_choices = {
        "TEXT_MODEL": [
            {"value": o.official_id, "label": o.label_zh} for o in TEXT_MODEL_OPTIONS
        ],
        "IMAGE_MODEL": [
            {"value": o.official_id, "label": o.label_zh} for o in IMAGE_MODEL_OPTIONS
        ],
    }
    return {"variables": variables, "modelChoices": model_choices}


@app.put("/settings/env")
async def put_env_settings(payload: dict):
    """寫入 .env 並立即套用至目前後端行程。values 可只包含要變更的鍵，其餘沿用檔案現值。"""
    raw = payload.get("values")
    if not isinstance(raw, dict):
        raise HTTPException(status_code=400, detail="values must be an object")
    for k in raw:
        if k not in MANAGED_ENV_KEYS:
            raise HTTPException(status_code=400, detail=f"unknown env key: {k}")

    project_root = _project_root()
    env_path = os.path.join(project_root, ".env")
    parsed = parse_env_file(env_path)
    merged: dict[str, str] = {
        spec.key: parsed.get(spec.key, "") for spec in MANAGED_ENV_VARS
    }
    for key, val in raw.items():
        if val is None:
            merged[key] = ""
        elif isinstance(val, str):
            merged[key] = val
        else:
            raise HTTPException(status_code=400, detail=f"invalid value for {key}")

    write_managed_env_file(env_path, merged)
    sync_managed_env_from_dotenv(env_path)
    return {"ok": True}


@app.put("/session-state")
async def put_session_state(payload: dict):
    """覆寫保存的 session 狀態。"""
    project_root = _project_root()
    saved = _save_session_state(project_root, payload)
    return {"ok": True, **saved}


@app.post("/upload-image")
async def upload_image(file: UploadFile = File(...), session_id: str | None = Form(None)):
    """接收單張圖片，存成 uploads/<sessionId>.jpg（無 sid 則回退 sample.jpg）。"""
    project_root = _project_root()
    dest_path = _sample_image_path_for_session(project_root, session_id)

    try:
        raw = await file.read()
        if not raw:
            raise HTTPException(status_code=400, detail="empty file")

        image = Image.open(io.BytesIO(raw))
        image.load()
        if image.mode in ("RGBA", "P"):
            image = image.convert("RGB")
        elif image.mode != "RGB":
            image = image.convert("RGB")

        image.save(dest_path, "JPEG", quality=92)
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"invalid image: {exc}") from exc

    return {"ok": True, "path": dest_path}


@app.get("/sample-reference")
async def get_sample_reference(session_id: str | None = None):
    """取得目前 session 的參考圖（無 sid 則回退 sample.jpg）。"""
    project_root = _project_root()
    path = _sample_image_path_for_session(project_root, session_id)
    if not os.path.exists(path):
        raise HTTPException(status_code=404, detail="sample.jpg not found")
    return FileResponse(path, media_type="image/jpeg")


@app.delete("/session-upload/{session_id}")
async def delete_session_upload(session_id: str):
    """刪除 uploads/<sessionId>.jpg 與 template_json/final_output_<sessionId>.json（不存在視為成功）。"""
    project_root = _project_root()
    sid = _safe_session_id(session_id)
    if not sid:
        raise HTTPException(status_code=400, detail="invalid session_id")
    upload_path = os.path.join(project_root, "uploads", f"{sid}.jpg")
    json_path = os.path.join(project_root, "template_json", f"final_output_{sid}.json")
    deleted_upload = False
    deleted_json = False
    if os.path.exists(upload_path):
        os.remove(upload_path)
        deleted_upload = True
    if os.path.exists(json_path):
        os.remove(json_path)
        deleted_json = True
    return {
        "ok": True,
        "deleted_upload": deleted_upload,
        "deleted_template_json": deleted_json,
        "upload_path": upload_path,
        "template_json_path": json_path,
    }


@app.delete("/session-upload/{session_id}/image")
async def delete_session_upload_image(session_id: str):
    """僅刪除 uploads/<sessionId>.jpg（不存在視為成功）。"""
    project_root = _project_root()
    sid = _safe_session_id(session_id)
    if not sid:
        raise HTTPException(status_code=400, detail="invalid session_id")
    upload_path = os.path.join(project_root, "uploads", f"{sid}.jpg")
    deleted_upload = False
    if os.path.exists(upload_path):
        os.remove(upload_path)
        deleted_upload = True
    return {
        "ok": True,
        "deleted_upload": deleted_upload,
        "upload_path": upload_path,
    }


@app.post("/session-upload/from-picture")
async def bind_session_upload_from_picture(payload: dict):
    """將 picture/<filename> 複製為 uploads/<sessionId>.jpg，供子討論串作為固定參考圖。"""
    session_id = payload.get("session_id")
    picture_filename = payload.get("picture_filename")
    sid = _safe_session_id(session_id)
    if not sid:
        raise HTTPException(status_code=400, detail="invalid session_id")
    if not isinstance(picture_filename, str) or not picture_filename.strip():
        raise HTTPException(status_code=400, detail="invalid picture_filename")

    project_root = _project_root()
    safe_name = os.path.basename(picture_filename.strip())
    src_path = os.path.join(project_root, "picture", safe_name)
    if not os.path.exists(src_path):
        raise HTTPException(status_code=404, detail="picture image not found")
    dest_path = _upload_image_path_for_session(project_root, sid)
    os.makedirs(os.path.dirname(dest_path), exist_ok=True)

    try:
        image = Image.open(src_path)
        image.load()
        if image.mode in ("RGBA", "P"):
            image = image.convert("RGB")
        elif image.mode != "RGB":
            image = image.convert("RGB")
        image.save(dest_path, "JPEG", quality=92)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"invalid picture image: {exc}") from exc

    return {"ok": True, "path": dest_path}


@app.post("/run")
async def run_generation(payload: dict):
    stage3_only = bool(payload.get("stage3_only", False))
    user_input = payload.get("user_input")
    session_id = payload.get("session_id")

    project_root = _project_root()
    sync_managed_env_from_dotenv(os.path.join(project_root, ".env"))
    config = parse_config(
        stage3_only_flag=stage3_only,
    )
    config = _apply_session_sample_path(config, session_id)

    try:
        result = await run_pipeline(config=config, user_input=user_input)
        return {
            "ok": True,
            "final_output_path": result["final_output_path"],
            "saved_files": result["saved_files"],
        }
    except Exception as exc:
        raise HTTPException(status_code=400, detail=_readable_error(exc)) from exc


@app.post("/run-stream")
async def run_generation_stream(payload: dict, request: Request):
    """以 SSE（text/event-stream）串流各階段進度與 AI 輸出片段。"""
    stage3_only = bool(payload.get("stage3_only", False))
    user_input = payload.get("user_input")
    session_id = payload.get("session_id")

    project_root = _project_root()
    sync_managed_env_from_dotenv(os.path.join(project_root, ".env"))
    config = parse_config(
        stage3_only_flag=stage3_only,
    )
    config = _apply_session_sample_path(config, session_id)

    async def event_stream():
        queue: asyncio.Queue = asyncio.Queue()
        loop = asyncio.get_running_loop()
        bus = ProgressBus(queue, loop)

        async def runner():
            try:
                result = await run_pipeline(
                    config=config,
                    user_input=user_input,
                    progress=bus,
                )
                await queue.put(
                    {
                        "type": "complete",
                        "saved_files": result["saved_files"],
                        "final_output_path": result["final_output_path"],
                    }
                )
            except Exception as exc:
                await queue.put({"type": "error", "detail": _readable_error(exc)})

        task = asyncio.create_task(runner())
        try:
            while True:
                if await request.is_disconnected():
                    task.cancel()
                    break
                try:
                    item = await asyncio.wait_for(queue.get(), timeout=0.25)
                except asyncio.TimeoutError:
                    continue
                yield f"data: {json.dumps(item, ensure_ascii=False)}\n\n"
                if item.get("type") in ("complete", "error"):
                    break
        finally:
            if not task.done():
                task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@app.get("/images/{filename}")
async def get_image(filename: str):
    project_root = _project_root()
    image_path = os.path.join(project_root, "picture", filename)
    if not os.path.exists(image_path):
        raise HTTPException(status_code=404, detail="image not found")
    return FileResponse(image_path)
