# CLAUDE.md — 專案持久指令

> 本檔案讓 Claude Code 在每次對話開始時自動載入專案架構、開發慣例與重要規則。

---

## 專案概述

**gnerate_pictures** 是一個以 Google Gemini 為核心的電商圖片生成平台。
前端為 React 多工具聊天介面；後端為 FastAPI，依工具分層組織業務邏輯。
目前已上線工具：**AI 電商圖文助手**（`ecommerce-image`）。

---

## 啟動指令

```powershell
# 後端（專案根目錄）
.\.venv\Scripts\python.exe -m uvicorn api.server:app --host 127.0.0.1 --port 8000 --reload

# 前端（frontend/ 目錄）
npm run dev
```

前端預設呼叫 `http://127.0.0.1:8000`，可於 `frontend/.env` 設定 `VITE_API_BASE_URL` 覆蓋。

---

## 後端架構分層

### 分層原則

```
core/          通用基礎設施（不含業務邏輯）
services/      通用服務（搜尋、影像 API）
api/
  server.py    只做 app 建立、CORS、startup、include_router
  deps.py      跨 router 共用工具函式
  routers/
    session.py   GET/PUT /session-state
    settings.py  GET/PUT /settings/env
    media.py     圖片上傳/下載/刪除
    tools/
      <工具名>/    工具專屬業務邏輯（自給自足）
        router.py
        pipeline.py
        stages/
        prompts/
        services/
        utils/
        image_thread/（子 session）
```

### 通用基礎設施（core/ 與 services/）

| 檔案 | 職責 |
|------|------|
| `core/config.py` | env 管理、`AppConfig`、模型清單、`.env` 讀寫 |
| `core/clients.py` | 依 config 組 Gemini API / Web client |
| `core/gemini_web_client.py` | Gemini 網頁版 HTTP 協定層 |
| `core/progress.py` | SSE 進度匯流排（`ProgressBus`） |
| `services/web_search.py` | Tavily / DuckDuckGo 搜尋與網頁抓取 |
| `services/image_gen.py` | 影像 API 呼叫與重試邏輯 |

### AI 電商圖文助手（`api/routers/tools/ecommerce_image/`）

| 檔案 | 職責 |
|------|------|
| `router.py` | POST /run、/run-stream（HTTP 入口） |
| `pipeline.py` | 三階段主流程編排 |
| `stages/stage1_gather.py` | 階段一：商品資訊蒐集（LLM + 搜尋） |
| `stages/stage2_json.py` | 階段二：P1~P9 JSON 腳本產生 |
| `stages/stage3_image.py` | 階段三：批次圖片生成 |
| `prompts/json_schema.py` | P1~P9 結構化 prompt 模板 |
| `prompts/image_style.py` | 電商視覺風格 prompt |
| `services/image_process.py` | prompt 組合與檔名安全化 |
| `utils/json_utils.py` | JSON 抽取、驗證（含 `EXPECTED_MAINS`）、LLM 修復 |
| `image_thread/router.py` | POST /image-thread/init、/chat/image-thread（子 session） |
| `image_thread/service.py` | image thread 歷史讀寫 |

---

## 新增工具規範

在 `api/routers/tools/` 下新建目錄，結構參照 `ecommerce_image/`：

```
api/routers/tools/<new_tool>/
├── router.py       ← HTTP 入口，在 api/server.py 加一行 include_router
├── pipeline.py     ← 工具主流程
├── stages/
├── prompts/
├── services/       ← 若需要工具專屬服務
└── utils/          ← 若需要工具專屬工具函式
```

引用通用層：`from core.xxx import ...`、`from services.web_search import ...`

---

## 前端架構

- **框架**：Vite + React 18 + TypeScript
- **工具定義**：`frontend/src/tools.ts` — `TOOLS` 陣列，每個工具一個 `ToolDefinition`
- **API 合約**：`frontend/src/api.ts` — 所有 HTTP/SSE 呼叫集中於此
- **型別**：`frontend/src/types/chatSession.ts`
- **狀態**：`frontend/src/chatStorage.ts`（localStorage 持久化）

新增工具時，在 `tools.ts` 的 `TOOLS` 陣列新增一筆 `ToolDefinition` 即可出現在側欄。

---

## API Endpoints

| 方法 | 路徑 | 說明 |
|------|------|------|
| GET/PUT | `/session-state` | 前端 session 狀態持久化 |
| GET/PUT | `/settings/env` | .env 讀寫與模型選項 |
| POST | `/upload-image` | 上傳參考商品圖 |
| GET | `/sample-reference` | 取得目前 session 參考圖 |
| DELETE | `/session-upload/{session_id}` | 清除 session 所有資源 |
| DELETE | `/session-upload/{session_id}/image` | 僅清除上傳圖 |
| POST | `/session-upload/from-picture` | 將生成圖設為 session 參考圖 |
| GET | `/images/{filename}` | 取得 picture/ 生成圖 |
| POST | `/run` | 同步執行電商圖文生成 |
| POST | `/run-stream` | SSE 串流執行電商圖文生成 |
| POST | `/image-thread/init` | 初始化子討論串 |
| POST | `/chat/image-thread` | SSE 子討論串圖片修改 |

---

## 重要開發慣例

- **`api/server.py` 不含業務邏輯**，只做 app 建立與 router 掛載
- **`api/deps.py`** 提供跨 router 共用函式（`project_root`、`safe_session_id`、`sse_streaming_response` 等）
- **工具業務邏輯自給自足**，只透過 `from core.xxx` 引用通用層，不跨工具互相引用
- **`EXPECTED_MAINS`**（P1~P9 欄位清單）定義在工具層 `utils/json_utils.py`，不在 `core/config.py`
- **SSE 串流**統一使用 `deps.py` 的 `sse_streaming_response(runner_fn, request)` 包裝
- **session_id** 格式限制：`[A-Za-z0-9_-]{1,128}`，由 `deps.safe_session_id()` 驗證
- **圖片輸出目錄**：`picture/`；上傳圖暫存：`uploads/<session_id>.jpg`
- **Python 環境**：`.venv`（使用 `.venv\Scripts\python.exe`）
- **回應語言**：繁體中文

---

## 執行期目錄（不進 git）

| 目錄/檔案 | 用途 |
|-----------|------|
| `picture/` | 圖片生成輸出 |
| `uploads/` | 上傳參考圖暫存 |
| `template_json/` | `final_output_<session_id>.json` |
| `data/session_state.json` | 前端 session 狀態持久化 |
| `data/image_thread_history_*.json` | 子討論串記憶 |
| `.env` | 環境變數（含 API 金鑰） |
