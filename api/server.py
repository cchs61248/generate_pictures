import io
import os

from fastapi import FastAPI, File, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from PIL import Image

from core.config import load_env_file, parse_config
from core.pipeline import run_pipeline


def _project_root() -> str:
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


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
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.get("/images/{filename}")
async def get_image(filename: str):
    project_root = _project_root()
    image_path = os.path.join(project_root, "picture", filename)
    if not os.path.exists(image_path):
        raise HTTPException(status_code=404, detail="image not found")
    return FileResponse(image_path)
