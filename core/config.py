import os
from dataclasses import dataclass


EXPECTED_MAINS = [
    "P1 首圖 CTR核心 Prompt",
    "P2 痛點 Pain Point Prompt",
    "P3 解決 Solution Prompt",
    "P4 場景 Context A Prompt",
    "P5 場景 Context B Prompt",
    "P6 細節 Close-up Prompt",
    "P7 比較 Comparison Prompt",
    "P8 延伸 Feature Prompt",
    "P9 規格 Specs Prompt",
]

TEXT_MODEL = "gemini-3-flash-preview"
IMAGE_MODEL = "gemini-3.1-flash-image-preview"


@dataclass
class AppConfig:
    project_root: str
    backend: str
    use_webapi: bool
    use_hybrid: bool
    stage3_only_mode: bool
    api_key: str
    psid: str
    psidts: str
    sample_image_path: str
    final_output_path: str
    picture_dir: str
    session_id: str


def load_env_file(env_path: str = ".env") -> None:
    if not os.path.exists(env_path):
        return
    with open(env_path, "r", encoding="utf-8") as file:
        for raw_line in file:
            line = raw_line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            key = key.strip()
            value = value.strip().strip('"').strip("'")
            if key and key not in os.environ:
                os.environ[key] = value


def parse_config(stage3_only_flag: bool) -> AppConfig:
    backend_raw = os.environ.get("GEMINI_BACKEND", "apikey")
    backend = (backend_raw or "apikey").lower().strip()
    if backend not in {"apikey", "webapi", "hybrid"}:
        print(
            f'⚠️ GEMINI_BACKEND 應為 apikey、webapi 或 hybrid，目前為「{backend_raw}」，將依 apikey 處理。'
        )
        backend = "apikey"

    stage3_only_mode = (
        os.environ.get("STAGE3_ONLY_MODE", "").lower() in {"1", "true", "yes"}
        or stage3_only_flag
    )

    project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    sample_image_path = os.path.join(project_root, "sample.jpg")
    final_output_path = os.path.join(project_root, "final_output.json")
    picture_dir = os.path.join(project_root, "picture")

    return AppConfig(
        project_root=project_root,
        backend=backend,
        use_webapi=(backend == "webapi"),
        use_hybrid=(backend == "hybrid"),
        stage3_only_mode=stage3_only_mode,
        api_key=os.environ.get("GOOGLE_API_KEY") or os.environ.get("GEMINI_API_KEY") or "",
        psid=os.environ.get("GEMINI_COOKIE_1PSID", ""),
        psidts=os.environ.get("GEMINI_COOKIE_1PSIDTS", ""),
        sample_image_path=sample_image_path,
        final_output_path=final_output_path,
        picture_dir=picture_dir,
        session_id="",
    )
