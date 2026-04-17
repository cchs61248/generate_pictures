"""
媒體資源 Router

負責：
  - POST   /upload-image                               上傳參考圖片
  - GET    /sample-reference                           取得目前 session 參考圖
  - DELETE /session-upload/{session_id}                清除 session 所有相關資源
  - DELETE /session-upload/{session_id}/image          僅清除上傳圖片
  - POST   /session-upload/from-picture                將已生成圖片設為 session 參考圖
  - GET    /images/{filename}                          取得 picture/ 目錄中的生成圖片
  - POST   /upload-document                            上傳附件文件（txt/pdf/docx/md）
  - DELETE /session-upload/{session_id}/document/{fn}  刪除單一文件
  - DELETE /session-upload/{session_id}/documents      刪除該 session 所有文件
"""
import io
import os
import uuid

from fastapi import APIRouter, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse
from PIL import Image

from api.deps import (
    doc_upload_path,
    project_root,
    safe_session_id,
    sample_image_path_for_session,
    upload_image_path_for_session,
)

_ALLOWED_DOC_EXTS = {".txt", ".pdf", ".docx", ".doc", ".md"}
from api.routers.tools.ecommerce_image.image_thread.service import (
    delete_image_thread_history,
)
from api.routers.tools.ecommerce_image.services.run_job import (
    cancel_ecommerce_run,
    delete_run_job_file,
)

router = APIRouter(tags=["media"])


@router.post("/upload-image")
async def upload_image(file: UploadFile = File(...), session_id: str | None = Form(None)):
    """接收單張圖片，存成 uploads/<sessionId>.jpg（無 sid 則回退 sample.jpg）。"""
    content_type = (file.content_type or "").lower()
    if not content_type.startswith("image/"):
        raise HTTPException(
            status_code=400,
            detail="請上傳圖片檔案（JPG、PNG、WebP 等），不支援此檔案格式。",
        )

    root = project_root()
    dest_path = sample_image_path_for_session(root, session_id)

    try:
        raw = await file.read()
        if not raw:
            raise HTTPException(status_code=400, detail="請上傳圖片檔案，檔案內容不能為空。")

        image = Image.open(io.BytesIO(raw))
        image.load()
        if image.mode not in ("RGB",):
            image = image.convert("RGB")
        image.save(dest_path, "JPEG", quality=92)
    except HTTPException:
        raise
    except Exception:
        raise HTTPException(
            status_code=400,
            detail="無法識別圖片，請上傳有效的圖片檔案（JPG、PNG、WebP 等）。",
        )

    return {"ok": True, "path": dest_path}


@router.get("/sample-reference")
async def get_sample_reference(session_id: str | None = None):
    """取得目前 session 的參考圖（無 sid 則回退 sample.jpg）。"""
    root = project_root()
    path = sample_image_path_for_session(root, session_id)
    if not os.path.exists(path):
        raise HTTPException(status_code=404, detail="sample.jpg not found")
    return FileResponse(path, media_type="image/jpeg")


def _delete_session_docs(root: str, sid: str) -> int:
    """刪除 uploads/<sid>_doc_* 所有文件檔，回傳刪除數量。"""
    uploads_dir = os.path.join(root, "uploads")
    if not os.path.isdir(uploads_dir):
        return 0
    prefix = f"{sid}_doc_"
    count = 0
    for fname in os.listdir(uploads_dir):
        if fname.startswith(prefix):
            try:
                os.remove(os.path.join(uploads_dir, fname))
                count += 1
            except OSError:
                pass
    return count


@router.delete("/session-upload/{session_id}")
async def delete_session_upload(session_id: str):
    """刪除 uploads/<sessionId>.jpg、doc 附件、final_output_*.json 與 image thread 歷史（不存在視為成功）。"""
    root = project_root()
    sid = safe_session_id(session_id)
    if not sid:
        raise HTTPException(status_code=400, detail="invalid session_id")

    await cancel_ecommerce_run(root, sid)
    delete_run_job_file(root, sid)

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
    deleted_docs = _delete_session_docs(root, sid)

    return {
        "ok": True,
        "deleted_upload": deleted_upload,
        "deleted_template_json": deleted_json,
        "deleted_image_thread_history": deleted_history,
        "deleted_docs": deleted_docs,
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


@router.post("/upload-document")
async def upload_document(file: UploadFile = File(...), session_id: str | None = Form(None)):
    """接收文件（txt/pdf/docx/md），存成 uploads/<sid>_doc_<uuid8>.<ext>，回傳 {ok, filename}。"""
    sid = safe_session_id(session_id)
    if not sid:
        raise HTTPException(status_code=400, detail="invalid session_id")

    original_name = file.filename or "document"
    ext = os.path.splitext(original_name)[1].lower()
    if ext not in _ALLOWED_DOC_EXTS:
        raise HTTPException(
            status_code=400,
            detail=f"不支援此文件格式，請上傳 txt、pdf、docx 或 md 檔案。",
        )

    short_id = uuid.uuid4().hex[:8]
    server_filename = f"{sid}_doc_{short_id}{ext}"
    root = project_root()
    dest_path = doc_upload_path(root, sid, f"{short_id}{ext}")

    raw = await file.read()
    if not raw:
        raise HTTPException(status_code=400, detail="檔案內容不能為空。")

    with open(dest_path, "wb") as f_out:
        f_out.write(raw)

    return {"ok": True, "filename": server_filename}


@router.delete("/session-upload/{session_id}/document/{filename}")
async def delete_session_document(session_id: str, filename: str):
    """刪除單一文件 uploads/<filename>（不存在視為成功）。"""
    root = project_root()
    sid = safe_session_id(session_id)
    if not sid:
        raise HTTPException(status_code=400, detail="invalid session_id")

    safe_name = os.path.basename(filename)
    if not safe_name.startswith(f"{sid}_doc_"):
        raise HTTPException(status_code=400, detail="invalid filename")

    file_path = os.path.join(root, "uploads", safe_name)
    deleted = False
    if os.path.exists(file_path):
        os.remove(file_path)
        deleted = True

    return {"ok": True, "deleted": deleted}


@router.delete("/session-upload/{session_id}/documents")
async def delete_session_documents(session_id: str):
    """刪除該 session 所有 doc 附件（不存在視為成功）。"""
    root = project_root()
    sid = safe_session_id(session_id)
    if not sid:
        raise HTTPException(status_code=400, detail="invalid session_id")

    deleted_count = _delete_session_docs(root, sid)
    return {"ok": True, "deleted_count": deleted_count}


@router.get("/images/{filename}")
async def get_image(filename: str):
    """取得 picture/ 目錄中已生成的圖片。"""
    root = project_root()
    image_path = os.path.join(root, "picture", filename)
    if not os.path.exists(image_path):
        raise HTTPException(status_code=404, detail="image not found")
    return FileResponse(image_path)
