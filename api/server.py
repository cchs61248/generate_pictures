"""
FastAPI 應用程式入口

職責：
  - 建立 FastAPI app 實例
  - 設定 CORS middleware
  - 掛載 startup 事件（載入 .env）
  - include 所有 router 模組

新增工具時，只需在 api/routers/tools/ 下建立新的工具目錄，
並在此檔案新增一行 app.include_router(...)。
"""
import os

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from api.deps import project_root
from api.routers import session, settings, media, token_usage
from api.routers.tools.ecommerce_image import router as ecommerce_image_router_module
from api.routers.tools.ecommerce_image.image_thread import router as image_thread_router_module
from core.config import sync_managed_env_from_dotenv

app = FastAPI(title="Generate Pictures API", version="0.1.0")


@app.on_event("startup")
async def startup_load_env():
    sync_managed_env_from_dotenv(os.path.join(project_root(), ".env"))


app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ── 共用 Router ────────────────────────────────────────────────────────────────
app.include_router(session.router)
app.include_router(settings.router)
app.include_router(media.router)
app.include_router(token_usage.router)

# ── 工具 Router（每個工具獨立掛載）────────────────────────────────────────────
app.include_router(ecommerce_image_router_module.router)
app.include_router(image_thread_router_module.router)
