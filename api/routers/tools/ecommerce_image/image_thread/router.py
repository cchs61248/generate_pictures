"""
AI 電商圖文助手 - Image Thread 子討論串 Router

負責：
  - POST /image-thread/init       初始化子討論串，將原始圖片路徑寫入記憶
  - POST /chat/image-thread        接收使用者指令，以 LLM 修改圖片並以 SSE 串流回應
"""
import asyncio
import io
import os
import time

from fastapi import APIRouter, HTTPException, Request
from PIL import Image

from api.deps import (
    project_root,
    readable_error,
    safe_filename_part,
    safe_session_id,
    sse_streaming_response,
)
from api.routers.tools.ecommerce_image.image_thread.service import (
    latest_image_path_from_entries,
    load_image_thread_history,
    save_image_thread_history,
)
from core.config import get_image_model, sync_managed_env_from_dotenv

router = APIRouter(tags=["ecommerce-image-thread"])


@router.post("/image-thread/init")
async def image_thread_init(payload: dict):
    """開啟子討論串時呼叫：將來源圖片路徑寫入該 session 的記憶系統。"""
    session_id = payload.get("session_id")
    picture_filename = payload.get("picture_filename")

    sid = safe_session_id(session_id)
    if not sid:
        raise HTTPException(status_code=400, detail="invalid session_id")
    if not isinstance(picture_filename, str) or not picture_filename.strip():
        raise HTTPException(status_code=400, detail="invalid picture_filename")

    safe_name = os.path.basename(picture_filename.strip())
    root = project_root()
    src_path = os.path.join(root, "picture", safe_name)
    if not os.path.exists(src_path):
        raise HTTPException(status_code=404, detail="picture image not found")

    entries = load_image_thread_history(root, sid)
    if not any(e.get("type") == "image" for e in entries):
        entries = [{"type": "image", "path": safe_name}]
        save_image_thread_history(root, sid, entries)

    return {"ok": True, "image_path": safe_name}


@router.post("/chat/image-thread")
async def chat_image_thread(payload: dict, request: Request):
    """子討論串專用：接收 session_id + user_text + session_title，以圖片 LLM 做圖片修改並以 SSE 串流回應。"""
    session_id = payload.get("session_id")
    user_text = payload.get("user_text", "").strip()
    session_title = payload.get("session_title", "").strip() or "thread"

    sid = safe_session_id(session_id)
    if not sid:
        raise HTTPException(status_code=400, detail="invalid session_id")
    if not user_text:
        raise HTTPException(status_code=400, detail="user_text is required")

    root = project_root()
    entries = load_image_thread_history(root, sid)

    latest_image_abs = latest_image_path_from_entries(root, entries)
    if not latest_image_abs or not os.path.exists(latest_image_abs):
        raise HTTPException(
            status_code=404,
            detail="找不到可用的參考圖片，請確認討論串已正確初始化。",
        )

    async def runner(queue: asyncio.Queue):
        try:
            from google.genai import types as genai_types
            import google.genai as genai_new

            sync_managed_env_from_dotenv(os.path.join(root, ".env"))
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

            with open(latest_image_abs, "rb") as img_f:
                ref_image_bytes = img_f.read()

            mime = "image/png" if latest_image_abs.lower().endswith(".png") else "image/jpeg"

            # 以最新圖片 + 本輪指令組成單輪 contents，避免圖片過多消耗 token
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
            for part in getattr(response, "parts", None) or []:
                if hasattr(part, "text") and part.text:
                    result_text += part.text
                elif hasattr(part, "inline_data") and part.inline_data is not None:
                    result_image_bytes = part.inline_data.data

            saved_filename: str | None = None
            if result_image_bytes:
                picture_dir = os.path.join(root, "picture")
                os.makedirs(picture_dir, exist_ok=True)
                safe_title = safe_filename_part(session_title)
                filename = f"{safe_title}_{sid}_{int(time.time())}.jpg"
                out_path = os.path.join(picture_dir, filename)
                img = Image.open(io.BytesIO(result_image_bytes))
                img.load()
                resized = img.resize((1000, 1000), Image.LANCZOS)
                if resized.mode != "RGB":
                    resized = resized.convert("RGB")
                resized.save(out_path, "JPEG", quality=92)
                saved_filename = filename

            current_entries = load_image_thread_history(root, sid)
            current_entries.append({"type": "user", "text": user_text})
            current_entries.append({
                "type": "model",
                "text": result_text or "",
                "image_path": saved_filename,
            })
            save_image_thread_history(root, sid, current_entries)

            await queue.put({
                "type": "complete",
                "text": result_text or "",
                "saved_image": saved_filename,
            })
        except Exception as exc:
            await queue.put({"type": "error", "detail": readable_error(exc)})

    return await sse_streaming_response(runner, request)
