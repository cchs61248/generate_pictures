# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

---

## 專案概述

**gnerate_pictures** 是一個以 Google Gemini 為核心的電商圖片生成平台。
前端為 React 多工具聊天介面；後端為 FastAPI，依工具分層組織業務邏輯。
目前已上線工具：**AI 電商圖文助手**（工具 id：`ecommerce-image`）。

使用者須先上傳商品圖，可再輸入描述或網址，並可附加 **txt / pdf / doc / docx / md** 說明文件，一併送入 pipeline。
> 注意：API 允許上傳 `.doc`，但目前文件抽文服務僅實作 `.txt/.md/.pdf/.docx`，`.doc` 內容不會被抽出供 LLM 使用。
各階段與子圖片討論串的 Gemini 呼叫會透過 `core/token_logger` 累計寫入 `data/token_usage.json`，並可由 `GET /token-usage` 查詢。

---

## 啟動指令

```powershell
# 後端 API（專案根目錄）
.\.venv\Scripts\python.exe -m uvicorn api.server:app --host 127.0.0.1 --port 8000 --reload

# 前端（frontend/ 目錄）
npm run dev
```

可選：不依 HTTP、直接跑 pipeline 的 CLI（根目錄 `main.py`，支援 `--stage3-only`）。

前端預設呼叫 `http://127.0.0.1:8000`，可於 `frontend/.env` 設定 `VITE_API_BASE_URL` 覆蓋。

---

## 後端架構分層

### 分層原則

```
core/          通用基礎設施（不含業務邏輯）
services/      通用服務（搜尋、影像 API、文件文字抽取）
api/
  server.py    只做 app 建立、CORS、startup、include_router（含工具主路由與子路由）
  deps.py      跨 router 共用工具函式
  routers/
    session.py GET/PUT /session-state
    settings.py     GET/PUT /settings/env
    media.py        圖片／文件上傳、下載、刪除、session 資源清理
    token_usage.py  GET /token-usage
    frontend_log.py POST /frontend-log
    tools/
      <工具名>/ 工具專屬業務邏輯（自給自足）
        router.py
        pipeline.py
        stages/
        prompts/
        services/
        utils/
        image_thread/   子圖片討論串（位於 ecommerce_image 下，於 server.py 單獨 include_router）
        style_learning/ 風格學習管理子路由（位於 ecommerce_image 下，於 server.py 單獨 include_router）
```

### 通用基礎設施（core/ 與 services/）

| 檔案 | 職責 |
|------|------|
| `core/config.py` | env 管理、`AppConfig`、模型清單（Gemini / OpenAI 兩套）、`.env` 讀寫；`get_text_provider()` / `get_image_provider()` 讀 `TEXT_PROVIDER` / `IMAGE_PROVIDER` 環境變數 |
| `core/clients.py` | `build_clients(config, require_text, require_image) → ClientBundle`；依供應商設定選擇 Gemini 或 OpenAI，回傳含 `text_provider` / `image_provider` 的 `ClientBundle` |
| `core/providers/base.py` | Provider 抽象基底：`TextProvider`（`chat_with_tools` / `generate_text`）、`ImageProvider`（`generate_image` / `edit_image`）；跨供應商統一的 `ContentItem`、`TextResult`、`ImageResult`、`EditResult` 資料類別 |
| `core/providers/gemini.py` | `GeminiTextProvider` / `GeminiImageProvider`：封裝 `google.genai` SDK；圖片生成含 429 / resource_exhausted 自動重試 |
| `core/providers/openai_compat.py` | `OpenAITextProvider` / `OpenAIImageProvider`：封裝 OpenAI SDK；支援 OpenAI 及任何 OpenAI-compatible API（Groq、OpenRouter、Ollama 等）；圖片修改使用 Responses API（`image_generation_call` 多輪狀態） |
| `core/progress.py` | SSE 進度匯流排（`ProgressBus`） |
| `core/token_logger.py` | `log_token_usage`、讀取 `data/token_usage.json`（thread-safe） |
| `core/app_logging.py` | 後端與前端日誌記錄設定 |
| `services/web_search.py` | Tavily / DuckDuckGo 搜尋與網頁抓取 |
| `services/image_gen.py` | 影像 API 呼叫與重試邏輯 |
| `services/document_reader.py` | 由路徑抽取文件純文字（供輸入 LLM prompt，有長度截斷） |

### AI 電商圖文助手（`api/routers/tools/ecommerce_image/`）

| 檔案 | 職責 |
|------|------|
| `router.py` | `POST /run`、`POST /run-stream`；payload 可含 `stage3_only`、`user_input`、`session_id`；會載入 session 文件文字 |
| `pipeline.py` | 三階段主流程編排 |
| `stages/stage1_gather.py` | 階段一：商品資訊蒐集（LLM + 搜尋）；token 紀錄 |
| `stages/stage2_json.py` | 階段二：P1~P9 JSON 腳本產生；token 紀錄 |
| `stages/stage3_image.py` | 階段三：批次圖片生成；token 紀錄 |
| `prompts/json_schema.py` | P1~P9 結構化 prompt 模板 |
| `prompts/image_style.py` | 電商視覺風格 prompt |
| `services/image_process.py` | prompt 組合與檔名安全化 |
| `services/style_learning.py` | 風格學習 queue/profile/history 管理、萃取與回滾 |
| `utils/json_utils.py` | JSON 抽取、驗證（含 `EXPECTED_MAINS`）、LLM 修復 |
| `services/run_job.py` | `/run-stream` 背景任務：與 HTTP 連線解耦、SSE 事件持久化（`data/run_job_ecommerce_image_<sid>.json`）、事件重播、任務取消 |
| `image_thread/router.py` | `POST /image-thread/init`、`POST /chat/image-thread`（SSE）；token 紀錄 |
| `image_thread/service.py` | image thread 歷史讀寫 |
| `image_thread/image_thread_job.py` | image thread 背景任務與事件佇列管理 |
| `style_learning/router.py` | style-learning 狀態、queue、extract、rollback、profile 管理 |

---

## 新增工具規範

在 `api/routers/tools/` 下新建目錄，結構參照 `ecommerce_image`：

```
api/routers/tools/<new_tool>/
├── router.py       ← HTTP 入口，在 api/server.py 加一行 app.include_router(...)
├── pipeline.py     ← 工具主流程
├── stages/
├── prompts/
├── services/       ← 若需要工具專屬服務
└── utils/          ← 若需要工具專屬工具函式
```

若工具有獨立子路由模組（類似 `image_thread` / `style_learning`），可在 `server.py` 另起一行 `include_router`，與主工具 router 分開掛載、標籤分離。

引用通用層：`from core.xxx import ...`、`from services.xxx import ...`

---

## 前端架構

- **框架**：Vite + React 18 + TypeScript
- **工具定義**：`frontend/src/tools.ts` — `TOOLS` 陣列，每個工具一個 `ToolDefinition`（目前 id 為 `ecommerce-image`）
- **API 合約**：`frontend/src/api.ts` — 所有 HTTP/SSE 呼叫、圖片／文件與 session 清理等
- **型別**：`frontend/src/types/chatSession.ts`
- **狀態**：`frontend/src/chatStorage.ts`（localStorage 與後端 `/session-state` 協調）
- **全畫面型 UI**：`SettingsPage.tsx`（環境設定 + 風格學習管理）、`TokenUsagePage.tsx`（用量查詢）

新增工具時，在 `tools.ts` 的 `TOOLS` 陣列新增一筆 `ToolDefinition` 即可出現在側欄。

---

## API Endpoints

| 方法 | 路徑 | 說明 |
|------|------|------|
| GET/PUT | `/session-state` | 前端 session 狀態持久化 |
| GET/PUT | `/settings/env` | `.env` 讀寫與模型選項 |
| GET | `/token-usage` | Token 用量列表；查詢參數 `start` / `end`（`YYYY-MM-DD`，可選） |
| POST | `/frontend-log` | 接收前端日誌 |
| POST | `/upload-image` | 上傳參考商品圖 → `uploads/<session_id>.jpg` |
| GET | `/sample-reference` | 取得目前 session 參考圖 |
| POST | `/upload-document` | 上傳附件（txt/pdf/doc/docx/md）→ `uploads/<sid>_doc_<uuid>.<ext>` |
| DELETE | `/session-upload/{session_id}` | 清除 session 上傳圖、文件、`template_json/final_output_*.json`、image thread 歷史 |
| DELETE | `/session-upload/{session_id}/image` | 僅清除上傳圖 |
| DELETE | `/session-upload/{session_id}/document/{filename}` | 刪除單一文件 |
| DELETE | `/session-upload/{session_id}/documents` | 刪除該 session 全部文件 |
| POST | `/session-upload/from-picture` | 將 `picture/` 生成圖複製為 session 參考圖 |
| GET | `/images/{filename}` | 取得 `picture/` 生成圖 |
| POST | `/run` | 同步執行電商圖文生成 |
| POST | `/run-stream` | SSE 串流執行電商圖文生成（背景任務，斷線不中止） |
| GET | `/run-stream/subscribe` | 重連訂閱既有 run 任務事件（`?session_id=&from_seq=`） |
| GET | `/run/status` | 取得 session 電商任務狀態 |
| GET | `/run/awaiting-plan` | 若任務停在「待選圖」，回傳 plan_ready items |
| POST | `/run/cancel` | 取消進行中背景 pipeline |
| POST | `/image-thread/init` | 初始化子討論串 |
| POST | `/chat/image-thread` | SSE 子討論串圖片修改 |
| GET | `/tools/ecommerce-image/style-learning/status` | 取得風格學習狀態（目前預設 profile、queue 統計） |
| GET | `/tools/ecommerce-image/style-learning/queue` | 分頁查詢學習事件佇列（`pending/extracted/all`） |
| DELETE | `/tools/ecommerce-image/style-learning/queue` | 批次刪除 queue 事件 |
| POST | `/tools/ecommerce-image/style-learning/queue/restore` | 將已 extracted 事件復原為 pending |
| GET | `/tools/ecommerce-image/style-learning/history` | 分頁查詢風格學習操作歷史 |
| POST | `/tools/ecommerce-image/style-learning/extract` | 由 pending 事件萃取新 profile |
| POST | `/tools/ecommerce-image/style-learning/rollback` | 切換預設 profile（回滾） |
| DELETE | `/tools/ecommerce-image/style-learning/profile` | 刪除指定 profile |
| PUT | `/tools/ecommerce-image/style-learning/profile` | 重新命名 profile |

---

## 重要開發慣例

- **雙供應商架構**：`TEXT_PROVIDER` / `IMAGE_PROVIDER` 可分別設為 `gemini`（預設）或 `openai`；業務邏輯只呼叫 `TextProvider` / `ImageProvider` 介面，不直接引用 SDK。新增供應商在 `core/providers/` 實作後，於 `core/clients.py` 的 `build_clients()` 中登記即可
- **`api/server.py` 不含業務邏輯**，只做 app 建立與 router 掛載（含 `ecommerce_image`、`image_thread`、`style_learning`）
- **`api/deps.py`** 提供跨 router 共用函式，例如：`project_root`、`safe_session_id`、`readable_error`、`sample_image_path_for_session`、`require_session_upload_exists`、`apply_session_sample_path`、`doc_upload_path`、`load_session_document_texts`、`load_session_documents`、`safe_filename_part`、`sse_streaming_response`、`sse_streaming_detached`
- **SSE 兩種包裝器**：`sse_streaming_response(generator_func, request)` — 客戶端斷線時取消背景任務；`sse_streaming_detached(event_source, request)` — 客戶端斷線時**不**取消，任務持續執行（電商主流程使用後者）
- **工具業務邏輯自給自足**，只透過 `from core.xxx`、`from services.xxx` 引用通用層，不跨工具互相引用
- **`EXPECTED_MAINS`**（P1~P9 欄位清單）定義在工具層 `utils/json_utils.py`，不在 `core/config.py`
- **SSE 串流**統一使用 `deps.py` 的 `sse_streaming_response(runner_fn, request)` 包裝
- **session_id** 格式限制：`[A-Za-z0-9_-]{1,128}`，由 `deps.safe_session_id()` 驗證
- **`/run` 與 `/run-stream` 前置條件**：須有合法 `session_id` 且已存在 `uploads/<session_id>.jpg`（避免未上傳時誤用根目錄 `sample.jpg`）
- **圖片輸出目錄**：`picture/`；上傳圖：`uploads/<session_id>.jpg`；文件：`uploads/<session_id>_doc_*`
- **Python 環境**：`.venv`（使用 `.venv\Scripts\python.exe`）
- **回應語言**：繁體中文

---

## 執行期目錄（不進 git）

| 目錄/檔案 | 用途 |
|-----------|------|
| `picture/` | 圖片生成輸出 |
| `uploads/` | 參考圖、文件附件 |
| `template_json/` | `final_output_<session_id>.json` |
| `data/session_state.json` | 前端 session 狀態持久化 |
| `data/image_thread_history_*.json` | 子討論串記憶 |
| `data/token_usage.json` | Token 用量紀錄 |
| `data/style_learning_queue_ecommerce_image.json` | 風格學習事件佇列（pending/extracted） |
| `data/style_profile_ecommerce_image.json` | 工具級風格偏好 profile（含 default） |
| `data/style_profile_ecommerce_image_versions.json` | 風格學習操作歷史（extract/rollback/rename/delete） |
| `data/run_job_ecommerce_image_<sid>.json` | 電商主流程背景任務事件紀錄（由 `run_job.py` 原子寫入） |
| `log/` | 系統與前端日誌檔案 |
| `.env` | 環境變數（含 API 金鑰）；`APP_RUNTIME_ROOT` 可覆寫根目錄（PaaS / 外掛 volume 部署用） |

