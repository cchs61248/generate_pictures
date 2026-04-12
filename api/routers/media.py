"""
媒體資源 Router

負責：
  - POST   /upload-image                      上傳參考圖片
  - GET    /sample-reference                  取得目前 session 參考圖
  - DELETE /session-upload/{session_id}        清除 session 所有相關資源
  - DELETE /session-upload/{session_id}/image  僅清除上傳圖片
  - POST   /session-upload/from-picture        將已生成圖片設為 session 參考圖
  - GET    /images/{filename}                  取得 picture/ 目錄中的生成圖片
"""
import io
import os

from fastapi import APIRouter, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse
from PIL import Image

from api.deps import (
    project_root,
    safe_session_id,
    sample_image_path_for_session,
    upload_image_path_for_session,
)
from api.routers.tools.ecommerce_image.image_thread.service import (
    delete_image_thread_history,
)

router = APIRouter(tags=["media"])


@router.post("/upload-image")
async def upload_image(file: UploadFile = File(...), session_id: str | None = Form(None)):
    """接收單張圖片，存成 uploads/<sessionId>.jpg（無 sid 則回退 sample.jpg）。"""
    root = project_root()
    dest_path = sample_image_path_for_session(root, session_id)

    try:
        raw = await file.read()
        if not raw:
            raise HTTPException(status_code=400, detail="empty file")

        image = Image.open(io.BytesIO(raw))
        image.load()
        if image.mode not in ("RGB",):
            image = image.convert("RGB")
        image.save(dest_path, "JPEG", quality=92)
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"invalid image: {exc}") from exc

    return {"ok": True, "path": dest_path}


@router.get("/sample-reference")
async def get_sample_reference(session_id: str | None = None):
    """取得目前 session 的參考圖（無 sid 則回退 sample.jpg）。"""
    root = project_root()
    path = sample_image_path_for_session(root, session_id)
    if not os.path.exists(path):
        raise HTTPException(status_code=404, detail="sample.jpg not found")
    return FileResponse(path, media_type="image/jpeg")


@router.delete("/session-upload/{session_id}")
async def delete_session_upload(session_id: str):
    """刪除 uploads/<sessionId>.jpg、final_output_*.json 與 image thread 歷史（不存在視為成功）。"""
    root = project_root()
    sid = safe_session_id(session_id)
    if not sid:
        raise HTTPException(status_code=400, detail="invalid session_id")

    upload_path = os.path.join(root, "uploads", f"{sid}.jpg")
    json_path = os.path.join(root, "template_json", f"final_output_{sid}.json")

    deleted_upload = False
    deleted_json = False
    if os.path.exists(upload_path):
        os.remove(upload_path)
        deleted_upload = True
    if os.path.exists(json_path):
        os.remove(json_path)
        deleted_json = True
    deleted_history = delete_image_thread_history(root, sid)

    return {
        "ok": True,
        "deleted_upload": deleted_upload,
        "deleted_template_json": deleted_json,
        "deleted_image_thread_history": deleted_history,
        "upload_path": upload_path,
        "template_json_path": json_path,
    }


@router.delete("/session-upload/{session_id}/image")
async def delete_session_upload_image(session_id: str):
    """僅刪除 uploads/<sessionId>.jpg（不存在視為成功）。"""
    root = project_root()
    sid = safe_session_id(session_id)
    if not sid:
        raise HTTPException(status_code=400, detail="invalid session_id")

    upload_path = os.path.join(root, "uploads", f"{sid}.jpg")
    deleted_upload = False
    if os.path.exists(upload_path):
        os.remove(upload_path)
        deleted_upload = True

    return {
        "ok": True,
        "deleted_upload": deleted_upload,
        "upload_path": upload_path,
    }


@router.post("/session-upload/from-picture")
async def bind_session_upload_from_picture(payload: dict):
    """將 picture/<filename> 複製為 uploads/<sessionId>.jpg，供子討論串作為固定參考圖。"""
    session_id = payload.get("session_id")
    picture_filename = payload.get("picture_filename")
    sid = safe_session_id(session_id)
    if not sid:
        raise HTTPException(status_code=400, detail="invalid session_id")
    if not isinstance(picture_filename, str) or not picture_filename.strip():
        raise HTTPException(status_code=400, detail="invalid picture_filename")

    root = project_root()
    safe_name = os.path.basename(picture_filename.strip())
    src_path = os.path.join(root, "picture", safe_name)
    if not os.path.exists(src_path):
        raise HTTPException(status_code=404, detail="picture image not found")
    dest_path = upload_image_path_for_session(root, sid)
    os.makedirs(os.path.dirname(dest_path), exist_ok=True)

    try:
        image = Image.open(src_path)
        image.load()
        if image.mode not in ("RGB",):
            image = image.convert("RGB")
        image.save(dest_path, "JPEG", quality=92)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"invalid picture image: {exc}") from exc

    return {"ok": True, "path": dest_path}


@router.get("/images/{filename}")
async def get_image(filename: str):
    """取得 picture/ 目錄中已生成的圖片。"""
    root = project_root()
    image_path = os.path.join(root, "picture", filename)
    if not os.path.exists(image_path):
        raise HTTPException(status_code=404, detail="image not found")
    return FileResponse(image_path)
