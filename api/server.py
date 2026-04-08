import os

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse

from core.config import load_env_file, parse_config
from core.pipeline import run_pipeline

app = FastAPI(title="Generate Pictures API", version="0.1.0")


@app.post("/run")
async def run_generation(payload: dict):
    stage3_only = bool(payload.get("stage3_only", False))
    user_input = payload.get("user_input")

    project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
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
    project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    image_path = os.path.join(project_root, "picture", filename)
    if not os.path.exists(image_path):
        raise HTTPException(status_code=404, detail="image not found")
    return FileResponse(image_path)
