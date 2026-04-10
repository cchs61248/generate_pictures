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
    get_image_model,
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


def _save_session_state(project_root: str, payload: dict) -> dict:
    sessions = payload.get("sessions")
    active_id = payload.get("activeId")
    if not isinstance(sessions, list) or not isinstance(active_id, str):
        raise HTTPException(status_code=400, detail="invalid session state payload")

    expected_version = payload.get("expectedVersion")
    if not isinstance(expected_version, int):
        raise HTTPException(status_code=400, detail="expectedVersion must be integer")

    # 合併傳入的 deletedIds 與現有檔案的 deletedIds（只累加，不清空）
    incoming_deleted = payload.get("deletedIds", [])
    if not isinstance(incoming_deleted, list):
        incoming_deleted = []

    current = _load_session_state(project_root)
    current_version = int(current.get("version", 0))
    if expected_version != current_version:
        raise HTTPException(status_code=409, detail="session state version conflict")

    existing_deleted = current.get("deletedIds", [])
    if not isinstance(existing_deleted, list):
        existing_deleted = []
    merged_deleted = list(dict.fromkeys(existing_deleted + incoming_deleted))  # 去重保序

    path = _session_state_path(project_root)
    temp_path = f"{path}.tmp"
    data = {
        "sessions": sessions,
        "activeId": active_id,
        "version": current_version + 1,
        "deletedIds": merged_deleted,
    }
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
    """刪除 uploads/<sessionId>.jpg、template_json/final_output_<sessionId>.json 與 image thread 記憶（不存在視為成功）。"""
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
    deleted_history = _delete_image_thread_history(project_root, sid)
    return {
        "ok": True,
        "deleted_upload": deleted_upload,
        "deleted_template_json": deleted_json,
        "deleted_image_thread_history": deleted_history,
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


# ── Image Thread 記憶系統 ─────────────────────────────────────────────────────
#
# 記憶格式（entries 陣列，每筆一條記錄）：
#   { "type": "image",  "path": "<picture 目錄內的相對路徑>" }
#   { "type": "user",   "text": "<使用者指令>" }
#   { "type": "model",  "text": "<LLM 說明文字>", "image_path": "<picture 內路徑 or null>" }
#
# 策略：
#   - init 時寫入第一條 image entry（原始圖片）
#   - 每次對話從 entries 末端往前找最新的 image path（init 圖 or 上輪 model 產出圖）
#   - LLM 產出圖後，存入 picture/，並在 model entry 記錄 image_path

def _image_thread_history_path(project_root: str, session_id: str) -> str:
    data_dir = os.path.join(project_root, "data")
    os.makedirs(data_dir, exist_ok=True)
    return os.path.join(data_dir, f"image_thread_history_{session_id}.json")


def _load_image_thread_history(project_root: str, session_id: str) -> list[dict]:
    path = _image_thread_history_path(project_root, session_id)
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


def _save_image_thread_history(project_root: str, session_id: str, entries: list[dict]) -> None:
    path = _image_thread_history_path(project_root, session_id)
    temp_path = f"{path}.tmp"
    with open(temp_path, "w", encoding="utf-8") as f:
        json.dump(entries, f, ensure_ascii=False)
    os.replace(temp_path, path)


def _delete_image_thread_history(project_root: str, session_id: str) -> bool:
    path = _image_thread_history_path(project_root, session_id)
    if os.path.exists(path):
        os.remove(path)
        return True
    return False


def _latest_image_path_from_entries(project_root: str, entries: list[dict]) -> str | None:
    """從記憶 entries 末端往前找最新的圖片路徑（絕對路徑），找不到回傳 None。"""
    picture_dir = os.path.join(project_root, "picture")
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


def _safe_filename_part(text: str, max_len: int = 40) -> str:
    """將任意字串轉成適合作為檔名的安全片段。"""
    safe = re.sub(r'[\\/:*?"<>|\s]+', "_", text.strip())
    return safe[:max_len] if safe else "thread"


@app.post("/image-thread/init")
async def image_thread_init(payload: dict):
    """開啟子討論串時呼叫：將來源圖片路徑寫入該 session 的記憶系統。"""
    session_id = payload.get("session_id")
    picture_filename = payload.get("picture_filename")  # picture/ 目錄內的檔名

    sid = _safe_session_id(session_id)
    if not sid:
        raise HTTPException(status_code=400, detail="invalid session_id")
    if not isinstance(picture_filename, str) or not picture_filename.strip():
        raise HTTPException(status_code=400, detail="invalid picture_filename")

    safe_name = os.path.basename(picture_filename.strip())
    project_root = _project_root()
    src_path = os.path.join(project_root, "picture", safe_name)
    if not os.path.exists(src_path):
        raise HTTPException(status_code=404, detail="picture image not found")

    entries = _load_image_thread_history(project_root, sid)
    # 若已初始化（已有記憶），不重複寫入
    if not any(e.get("type") == "image" for e in entries):
        entries = [{"type": "image", "path": safe_name}]
        _save_image_thread_history(project_root, sid, entries)

    return {"ok": True, "image_path": safe_name}


@app.post("/chat/image-thread")
async def chat_image_thread(payload: dict, request: Request):
    """子討論串專用：接收 session_id + user_text + session_title，以圖片 LLM 做圖片修改並以 SSE 串流回應。"""
    session_id = payload.get("session_id")
    user_text = payload.get("user_text", "").strip()
    session_title = payload.get("session_title", "").strip() or "thread"

    sid = _safe_session_id(session_id)
    if not sid:
        raise HTTPException(status_code=400, detail="invalid session_id")
    if not user_text:
        raise HTTPException(status_code=400, detail="user_text is required")

    project_root = _project_root()
    entries = _load_image_thread_history(project_root, sid)

    # 從記憶找最新圖片路徑
    latest_image_abs = _latest_image_path_from_entries(project_root, entries)
    if not latest_image_abs or not os.path.exists(latest_image_abs):
        raise HTTPException(
            status_code=404,
            detail="找不到可用的參考圖片，請確認討論串已正確初始化。",
        )

    async def event_stream():
        queue: asyncio.Queue = asyncio.Queue()

        async def runner():
            try:
                from google.genai import types as genai_types
                import google.genai as genai_new
                import time as _time

                sync_managed_env_from_dotenv(os.path.join(project_root, ".env"))
                api_key = os.environ.get("GOOGLE_API_KEY") or os.environ.get("GEMINI_API_KEY") or ""
                if not api_key:
                    raise ValueError("未設定 GOOGLE_API_KEY 或 GEMINI_API_KEY，請至設定頁填寫。")

                client = genai_new.Client(api_key=api_key)
                model_name = get_image_model()

                system_instruction = (
                    "你是一位專業的電商圖片修改助手。使用者會提供一張商品圖片，並以文字描述希望如何修改。\n"
                    "請依照使用者的描述修改圖片，並回傳修改後的圖片。\n"
                    "若使用者的描述不夠清楚，可以先以文字詢問補充資訊，同時也提供一個依目前理解修改的版本。\n"
                    "回應時請先以簡短文字說明你做了哪些修改，再提供圖片。"
                )

                # 載入最新參考圖
                with open(latest_image_abs, "rb") as img_f:
                    ref_image_bytes = img_f.read()

                # 判斷 MIME 類型
                lower_name = latest_image_abs.lower()
                mime = "image/png" if lower_name.endswith(".png") else "image/jpeg"

                # 組合本輪 contents（單輪：參考圖 + 使用者指令）
                # 多輪記憶以「只帶最新圖片 + 本輪指令」方式送給 LLM，
                # 避免圖片過多消耗 token；文字歷史以 system_instruction 補充說明。
                # 若需要完整文字歷史，可在 system_instruction 附加摘要。
                contents = [
                    genai_types.Content(
                        role="user",
                        parts=[
                            genai_types.Part.from_bytes(data=ref_image_bytes, mime_type=mime),
                            genai_types.Part.from_text(text=user_text),
                        ],
                    )
                ]

                await queue.put({"type": "progress", "content": "正在處理圖片，請稍候…"})

                response = await asyncio.to_thread(
                    client.models.generate_content,
                    model=model_name,
                    contents=contents,
                    config=genai_types.GenerateContentConfig(
                        system_instruction=system_instruction,
                        response_modalities=["TEXT", "IMAGE"],
                    ),
                )

                result_text = ""
                result_image_bytes: bytes | None = None
                for part in response.parts:
                    if hasattr(part, "text") and part.text:
                        result_text += part.text
                    elif hasattr(part, "inline_data") and part.inline_data is not None:
                        result_image_bytes = part.inline_data.data

                # 儲存產出圖片
                saved_filename: str | None = None
                if result_image_bytes:
                    picture_dir = os.path.join(project_root, "picture")
                    os.makedirs(picture_dir, exist_ok=True)
                    safe_title = _safe_filename_part(session_title)
                    filename = f"{safe_title}_{sid}_{int(_time.time())}.jpg"
                    out_path = os.path.join(picture_dir, filename)
                    img = Image.open(io.BytesIO(result_image_bytes))
                    img.load()
                    resized = img.resize((1000, 1000), Image.LANCZOS)
                    if resized.mode != "RGB":
                        resized = resized.convert("RGB")
                    resized.save(out_path, "JPEG", quality=92)
                    saved_filename = filename

                # 更新記憶
                current_entries = _load_image_thread_history(project_root, sid)
                current_entries.append({"type": "user", "text": user_text})
                current_entries.append({
                    "type": "model",
                    "text": result_text or "",
                    "image_path": saved_filename,
                })
                _save_image_thread_history(project_root, sid, current_entries)

                await queue.put({
                    "type": "complete",
                    "text": result_text or "",
                    "saved_image": saved_filename,
                })
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
