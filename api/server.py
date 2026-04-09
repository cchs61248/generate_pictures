import asyncio
import io
import json
import os

from fastapi import FastAPI, File, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, StreamingResponse
from PIL import Image

from core.config import load_env_file, parse_config
from core.pipeline import run_pipeline
from core.progress import ProgressBus


def _project_root() -> str:
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _readable_error(exc: Exception) -> str:
    detail = str(exc).strip()
    name = exc.__class__.__name__
    if detail:
        if detail == name:
            return detail
        return f"{name}: {detail}"
    return name


app = FastAPI(title="Generate Pictures API", version="0.1.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.post("/upload-image")
async def upload_image(file: UploadFile = File(...)):
    """接收單張圖片，存成專案根目錄 sample.jpg（供 pipeline 使用）。"""
    project_root = _project_root()
    dest_path = os.path.join(project_root, "sample.jpg")

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
async def get_sample_reference():
    """目前寫入專案根目錄的 sample.jpg（供輸入區預覽後備）。"""
    project_root = _project_root()
    path = os.path.join(project_root, "sample.jpg")
    if not os.path.exists(path):
        raise HTTPException(status_code=404, detail="sample.jpg not found")
    return FileResponse(path, media_type="image/jpeg")


@app.post("/run")
async def run_generation(payload: dict):
    stage3_only = bool(payload.get("stage3_only", False))
    user_input = payload.get("user_input")

    project_root = _project_root()
    load_env_file(os.path.join(project_root, ".env"))
    config = parse_config(
        stage3_only_flag=stage3_only,
    )

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
async def run_generation_stream(payload: dict):
    """以 SSE（text/event-stream）串流各階段進度與 AI 輸出片段。"""
    stage3_only = bool(payload.get("stage3_only", False))
    user_input = payload.get("user_input")

    project_root = _project_root()
    load_env_file(os.path.join(project_root, ".env"))
    config = parse_config(
        stage3_only_flag=stage3_only,
    )

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
                item = await queue.get()
                yield f"data: {json.dumps(item, ensure_ascii=False)}\n\n"
                if item.get("type") in ("complete", "error"):
                    break
        finally:
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
