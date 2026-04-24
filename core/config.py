import os
from dataclasses import dataclass

@dataclass(frozen=True)
class ManagedEnvVar:
    """後端與設定頁共同使用的環境變數定義（鍵名 + 給使用者看的說明）。"""

    key: str
    description: str


@dataclass(frozen=True)
class ModelOption:
    """API 官方 model 字串 + 介面顯示名稱（與社群常用稱呼對齊）。"""

    official_id: str
    label_zh: str


# 階段一／二 等文字模型（Gemini）
TEXT_MODEL_OPTIONS: tuple[ModelOption, ...] = (
    ModelOption("gemini-3-flash-preview", "Gemini 3 Flash"),
    ModelOption("gemini-3.1-flash-lite-preview", "Gemini 3.1 Flash-Lite"),
    ModelOption("gemini-3.1-pro-preview", "Gemini 3.1 Pro"),
    ModelOption("gemini-2.5-flash", "Gemini 2.5 Flash"),
    ModelOption("gemini-2.5-pro", "Gemini 2.5 Pro"),
)

# 階段三 API 產圖（Gemini；Nano Banana 為文件用語）
IMAGE_MODEL_OPTIONS: tuple[ModelOption, ...] = (
    ModelOption("gemini-3.1-flash-image-preview", "Nano Banana 2"),
    ModelOption("gemini-3-pro-image-preview", "Nano Banana Pro"),
    ModelOption("gemini-2.5-flash-image", "Nano Banana"),
)

DEFAULT_TEXT_MODEL = "gemini-3-flash-preview"
DEFAULT_IMAGE_MODEL = "gemini-3.1-flash-image-preview"

# OpenAI / OpenAI-compatible 文字模型
OPENAI_TEXT_MODEL_OPTIONS: tuple[ModelOption, ...] = (
    ModelOption("gpt-5.4", "GPT-5.4"),
    ModelOption("gpt-5.4-mini", "GPT-5.4 Mini"),
    ModelOption("gpt-5.4-nano", "GPT-5.4 Nano"),
    ModelOption("gpt-5.4-pro", "GPT-5.4 Pro"),
    ModelOption("o4-mini", "o4 Mini"),
    ModelOption("o3", "o3"),
)

# OpenAI 圖像模型
OPENAI_IMAGE_MODEL_OPTIONS: tuple[ModelOption, ...] = (
    ModelOption("gpt-image-2", "GPT Image 2（新版 Edits）"),
    ModelOption("gpt-image-1.5", "GPT Image 1.5（新版 Edits）"),
    ModelOption("gpt-image-1", "GPT Image 1"),
    ModelOption("gpt-image-1-mini", "GPT Image 1 Mini"),
)

DEFAULT_OPENAI_TEXT_MODEL = "gpt-5.4-mini"
DEFAULT_OPENAI_IMAGE_MODEL = "gpt-image-2"


def get_text_provider() -> str:
    """TEXT_PROVIDER 環境變數；預設 gemini。"""
    return (os.environ.get("TEXT_PROVIDER") or "gemini").strip().lower()


def get_image_provider() -> str:
    """IMAGE_PROVIDER 環境變數；預設 gemini。"""
    return (os.environ.get("IMAGE_PROVIDER") or "gemini").strip().lower()


def get_openai_api_key() -> str:
    return (os.environ.get("OPENAI_API_KEY") or "").strip()


def get_openai_base_url() -> str | None:
    raw = (os.environ.get("OPENAI_BASE_URL") or "").strip()
    return raw if raw else None


def get_text_model() -> str:
    """TEXT_MODEL 環境變數；空值依供應商用預設。非清單內字串仍允許（手改 .env 進階用法）。"""
    raw = (os.environ.get("TEXT_MODEL") or "").strip()
    if not raw:
        return DEFAULT_OPENAI_TEXT_MODEL if get_text_provider() == "openai" else DEFAULT_TEXT_MODEL
    return raw


def get_image_model() -> str:
    """IMAGE_MODEL 環境變數；空值依供應商用預設。非清單內字串仍允許。"""
    raw = (os.environ.get("IMAGE_MODEL") or "").strip()
    if not raw:
        return DEFAULT_OPENAI_IMAGE_MODEL if get_image_provider() == "openai" else DEFAULT_IMAGE_MODEL
    return raw


def get_image_output_size() -> str:
    """IMAGE_OUTPUT_SIZE：Gemini 產圖像素等級，僅支援 1K / 2K / 4K（預設 1K）。較高解析有助筆畫較多的中文。"""
    raw = (os.environ.get("IMAGE_OUTPUT_SIZE") or "1K").strip().upper()
    if raw in ("512", "1K", "2K", "4K"):
        return raw
    return "1K"


# 與 parse_config、web_search、用戶端等讀取的變數對齊；順序即寫入 .env 的順序。
MANAGED_ENV_VARS: tuple[ManagedEnvVar, ...] = (
    ManagedEnvVar(
        "GOOGLE_API_KEY",
        "Gemini 的 API 金鑰；使用 Gemini 供應商時必填。",
    ),
    ManagedEnvVar(
        "STAGE3_ONLY_MODE",
        "設為 1、true 或 yes 時僅執行階段三（依既有 JSON 產圖）；否則為完整管線。",
    ),
    ManagedEnvVar(
        "TAVILY_API_KEY",
        "Tavily 網路搜尋 API 金鑰；用於商品資訊搜尋等步驟。未填則相關功能可能略過或失敗。",
    ),
    ManagedEnvVar(
        "MAX_LLM_SEARCH_CALLS",
        "階段一 LLM 自動呼叫中，search_web 實際查詢次數上限（0～9 的整數；預設 3）。不含觸發上限時回傳的系統訊息。",
    ),
    ManagedEnvVar(
        "TEXT_PROVIDER",
        "文字模型供應商：gemini（預設）或 openai。",
    ),
    ManagedEnvVar(
        "IMAGE_PROVIDER",
        "圖像模型供應商：gemini（預設）或 openai。",
    ),
    ManagedEnvVar(
        "TEXT_MODEL",
        "文字模型代碼；空值依供應商自動填入預設值。",
    ),
    ManagedEnvVar(
        "IMAGE_MODEL",
        "圖像模型代碼；空值依供應商自動填入預設值。",
    ),
    ManagedEnvVar(
        "IMAGE_OUTPUT_SIZE",
        "API 產圖輸出尺寸：512(中文可能變形)、1K、2K 或 4K（預設 1K）。含大量中文時可試 2K 以減少字元變形。",
    ),
    ManagedEnvVar(
        "OPENAI_API_KEY",
        "OpenAI 或相容 API 的金鑰；任一供應商設為 openai 時必填。",
    ),
    ManagedEnvVar(
        "OPENAI_BASE_URL",
        "OpenAI-compatible API 端點（留空則使用 https://api.openai.com/v1）。可填入 Groq、OpenRouter、Ollama 等相容端點。",
    ),
)

MANAGED_ENV_KEYS: frozenset[str] = frozenset(v.key for v in MANAGED_ENV_VARS)

# 仍由後端寫入 .env 並套用，但不在 GET /settings/env 與前端設定表單顯示。
ENV_VARS_HIDDEN_FROM_SETTINGS_UI: frozenset[str] = frozenset(
    {
        "STAGE3_ONLY_MODE",
    }
)

# TEXT_MODEL / IMAGE_MODEL 的下拉選項（依供應商分組），供設定 API 回傳前端使用。
PROVIDER_MODEL_CHOICES: dict[str, dict[str, list[dict[str, str]]]] = {
    "TEXT_MODEL": {
        "gemini": [{"value": o.official_id, "label": o.label_zh} for o in TEXT_MODEL_OPTIONS],
        "openai": [{"value": o.official_id, "label": o.label_zh} for o in OPENAI_TEXT_MODEL_OPTIONS],
    },
    "IMAGE_MODEL": {
        "gemini": [{"value": o.official_id, "label": o.label_zh} for o in IMAGE_MODEL_OPTIONS],
        "openai": [{"value": o.official_id, "label": o.label_zh} for o in OPENAI_IMAGE_MODEL_OPTIONS],
    },
}


def resolve_project_root() -> str:
    """專案資料根目錄。若設定 APP_RUNTIME_ROOT（如 PaaS 掛載 volume），則使用該路徑。"""
    runtime_root = (os.environ.get("APP_RUNTIME_ROOT") or "").strip()
    if runtime_root:
        return os.path.abspath(runtime_root)
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


@dataclass
class AppConfig:
    project_root: str
    stage3_only_mode: bool
    api_key: str
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


def parse_env_file(env_path: str) -> dict[str, str]:
    """讀取 .env 內 KEY=value，忽略註解與空行；重複鍵以最後一筆為準。"""
    out: dict[str, str] = {}
    if not os.path.exists(env_path):
        return out
    with open(env_path, "r", encoding="utf-8") as file:
        for raw_line in file:
            line = raw_line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            key = key.strip()
            value = value.strip().strip('"').strip("'")
            if key:
                out[key] = value
    return out


def _format_env_value(value: str) -> str:
    if not value:
        return ""
    if any(c in value for c in '\n\r#"') or value != value.strip():
        escaped = value.replace("\\", "\\\\").replace('"', '\\"')
        return f'"{escaped}"'
    return value


def write_managed_env_file(env_path: str, values: dict[str, str]) -> None:
    """依 MANAGED_ENV_VARS 順序寫入 .env，每個變數上方附說明註解。
    檔案中原有、但未列入 MANAGED_ENV_KEYS 的鍵會附加在末尾保留。"""
    prev = parse_env_file(env_path) if os.path.exists(env_path) else {}
    lines: list[str] = []
    for spec in MANAGED_ENV_VARS:
        raw = values.get(spec.key, "")
        lines.append(f"# {spec.description}")
        lines.append(f"{spec.key}={_format_env_value(raw)}")
        lines.append("")
    extras = {k: v for k, v in prev.items() if k not in MANAGED_ENV_KEYS}
    if extras:
        lines.append("# 以下變數未在設定表單中列出，仍保留於檔案中。")
        lines.append("")
        for key in sorted(extras):
            lines.append(f"{key}={_format_env_value(extras[key])}")
            lines.append("")
    parent = os.path.dirname(os.path.abspath(env_path))
    if parent:
        os.makedirs(parent, exist_ok=True)
    temp_path = f"{env_path}.tmp"
    with open(temp_path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines).rstrip() + "\n")
    os.replace(temp_path, env_path)


def apply_managed_env(values: dict[str, str]) -> None:
    """將受管理變數寫入目前行程的 os.environ（覆寫），使後續 parse_config 等讀到最新值。"""
    for spec in MANAGED_ENV_VARS:
        key = spec.key
        os.environ[key] = values.get(key, "")


def sync_managed_env_from_dotenv(env_path: str) -> None:
    """先依傳統規則載入檔案，再強制以檔案中受管理鍵覆寫行程環境。"""
    load_env_file(env_path)
    parsed = parse_env_file(env_path)
    merged = {spec.key: parsed.get(spec.key, "") for spec in MANAGED_ENV_VARS}
    apply_managed_env(merged)


def parse_config(stage3_only_flag: bool) -> AppConfig:
    stage3_only_mode = (
        os.environ.get("STAGE3_ONLY_MODE", "").lower() in {"1", "true", "yes"}
        or stage3_only_flag
    )

    project_root = resolve_project_root()
    sample_image_path = os.path.join(project_root, "sample.jpg")
    final_output_path = os.path.join(project_root, "final_output.json")
    picture_dir = os.path.join(project_root, "picture")

    return AppConfig(
        project_root=project_root,
        stage3_only_mode=stage3_only_mode,
        api_key=os.environ.get("GOOGLE_API_KEY") or "",
        sample_image_path=sample_image_path,
        final_output_path=final_output_path,
        picture_dir=picture_dir,
        session_id="",
    )
