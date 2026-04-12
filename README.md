# gnerate_pictures

以 **Google Gemini** 為核心的電商圖片生成平台。
前端為 React 多工具聊天介面，後端為 FastAPI，業務邏輯依工具分層組織。

目前已上線工具：**AI 電商圖文助手** — 上傳商品圖片與描述，自動生成 P1~P9 共 9 款電商風格圖。

---

## 環境需求

- Python 虛擬環境（專案內 `.venv`）
- Node.js LTS（前端開發用）
- 安裝後端依賴：`pip install -r requirements.txt`
- 安裝前端依賴：`cd frontend && npm install`

---

## 環境變數設定

將 `.env.example` 複製為 `.env`，填入必要金鑰：

| 變數 | 說明 |
|------|------|
| `GOOGLE_API_KEY` | Gemini API 金鑰（apikey / hybrid 模式） |
| `GEMINI_API_KEY` | 與 GOOGLE_API_KEY 擇一即可 |
| `GEMINI_BACKEND` | `apikey`（預設）/ `hybrid` / `webapi` |
| `TAVILY_API_KEY` | 網路搜尋金鑰（階段一用，選填） |
| `TEXT_MODEL` | 文字模型（留空用預設） |
| `IMAGE_MODEL` | 圖像模型（留空用預設） |
| `GEMINI_COOKIE_1PSID` | webapi / hybrid 模式的瀏覽器 Cookie |
| `GEMINI_COOKIE_1PSIDTS` | webapi / hybrid 模式的瀏覽器 Cookie |

前端環境變數：`frontend/.env`（複製自 `frontend/.env.example`）

| 變數 | 說明 |
|------|------|
| `VITE_API_BASE_URL` | 後端位址（預設 `http://127.0.0.1:8000`） |

---

## 啟動方式

請分別開兩個終端機：

### 後端（FastAPI）

```powershell
.\.venv\Scripts\python.exe -m uvicorn api.server:app --host 127.0.0.1 --port 8000 --reload
```

### 前端（Vite）

```powershell
cd frontend
npm run dev
```

前端預設開在 `http://localhost:5173`。

---

## 專案架構

```
gnerate_pictures/
│
├── main.py                   CLI 入口（可直接執行三階段管線）
├── requirements.txt
│
├── core/                     通用基礎設施
│   ├── config.py             env 管理、AppConfig、模型清單
│   ├── clients.py            組 Gemini API / Web client
│   ├── gemini_web_client.py  Gemini 網頁版 HTTP 協定層
│   └── progress.py           SSE 進度匯流排（ProgressBus）
│
├── services/                 通用服務
│   ├── web_search.py         Tavily / DuckDuckGo 搜尋與網頁抓取
│   └── image_gen.py          影像 API 呼叫與重試
│
├── api/                      HTTP 層
│   ├── server.py             FastAPI app、CORS、router 掛載
│   ├── deps.py               跨 router 共用 helper
│   └── routers/
│       ├── session.py        GET/PUT /session-state
│       ├── settings.py       GET/PUT /settings/env
│       ├── media.py          圖片上傳/下載/刪除
│       └── tools/
│           └── ecommerce_image/           AI 電商圖文助手
│               ├── router.py              POST /run、/run-stream
│               ├── pipeline.py            三階段主流程編排
│               ├── stages/
│               │   ├── stage1_gather.py   商品資訊蒐集
│               │   ├── stage2_json.py     P1~P9 JSON 產生
│               │   └── stage3_image.py    批次圖片生成
│               ├── prompts/
│               │   ├── json_schema.py     P1~P9 結構化 prompt
│               │   └── image_style.py     電商視覺風格 prompt
│               ├── services/
│               │   └── image_process.py   prompt 組合
│               ├── utils/
│               │   └── json_utils.py      JSON 驗證、修復、EXPECTED_MAINS
│               └── image_thread/          圖片子討論串（子 session）
│                   ├── router.py          POST /image-thread/init、/chat/image-thread
│                   └── service.py         子討論串記憶讀寫
│
└── frontend/                 Vite + React + TypeScript
    └── src/
        ├── tools.ts          工具定義清單（TOOLS 陣列）
        ├── api.ts            所有 HTTP/SSE 呼叫集中於此
        ├── chatStorage.ts    localStorage 持久化
        └── components/       ChatWindow、Sidebar、InputBar 等
```

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
| POST | `/image-thread/init` | 初始化圖片子討論串 |
| POST | `/chat/image-thread` | SSE 子討論串圖片修改 |

---

## CLI 用法

```powershell
# 完整三階段執行
.\.venv\Scripts\python.exe main.py

# 僅執行階段三（需已有 final_output.json）
.\.venv\Scripts\python.exe main.py --stage3-only
```

---

## 新增工具

1. 在 `api/routers/tools/` 下新建 `<tool_name>/` 目錄
2. 建立 `router.py`，在 `api/server.py` 加一行 `app.include_router(...)`
3. 在 `frontend/src/tools.ts` 的 `TOOLS` 陣列新增一筆 `ToolDefinition`

工具業務邏輯完全自給自足於工具目錄內；共用能力透過 `from core.xxx` 引用。

---

## 執行期目錄（不進 git）

| 目錄/檔案 | 用途 |
|-----------|------|
| `picture/` | 圖片生成輸出（`P01_*.png` ~ `P09_*.png`） |
| `uploads/` | 上傳參考圖暫存（`<session_id>.jpg`） |
| `template_json/` | `final_output_<session_id>.json` |
| `data/session_state.json` | 前端 session 狀態持久化 |
| `data/image_thread_history_*.json` | 子討論串記憶 |
| `.env` | 環境變數（含 API 金鑰，勿提交） |

---

## 注意事項

- 請先啟動後端，再啟動前端
- `GOOGLE_API_KEY` 或 `GEMINI_API_KEY` 至少填一個才能執行
- 使用 `webapi` 或 `hybrid` 模式需要有效的瀏覽器 Cookie
- uvicorn `--reload` 模式下若刪除目錄後需重啟伺服器
