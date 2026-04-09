# gnerate_pictures

`gnerate_pictures` 是一個以 Gemini 為核心的商品圖生成專案，目標是把商品資訊整理成 P1～P9 的內容，再輸出九宮格行銷圖片。

## 專案做什麼

- 階段一：讀取商品參考圖（預設 `sample.jpg`）與使用者輸入（文字或網址）來蒐集資訊
- 階段二：依 `prompts\json_schema.py` 生成標準化 JSON（P1～P9）
- 階段三：依 `prompts\image_style.py` 轉成圖片 prompt，輸出圖片到 `picture\`
- 同時提供 FastAPI 後端與 React 前端，支援本機互動式操作

## 主要結構

- `main.py`：CLI 入口，協調整體流程
- `core\`：設定、模型 client 初始化、pipeline 協調
- `stages\`：三階段實作（蒐集 / JSON / 產圖）
- `services\`：搜尋、重試、圖片處理等共用服務
- `utils\json_utils.py`：JSON 抽取、驗證、修復
- `prompts\`：文字生成與圖片風格 prompt
- `api\server.py`：FastAPI 服務
- `frontend\`：Vite + React + TypeScript 前端介面
- `picture\`：輸出圖片目錄
- `final_output.json`：P1～P9 最終結構化輸出

## 環境需求

- Windows（建議使用專案虛擬環境 `.venv`）
- Node.js（前端開發用）
- 已安裝 `requirements.txt` 內 Python 套件與前端 `npm` 依賴

## 環境變數設定

請將 `.env.example` 複製為 `.env`，並填入必要金鑰與設定。

常用欄位：

- `GEMINI_BACKEND`：`apikey` / `webapi` / `hybrid`
- `GOOGLE_API_KEY` 或 `GEMINI_API_KEY`：`apikey`、`hybrid` 常用
- `GEMINI_COOKIE_1PSID`、`GEMINI_COOKIE_1PSIDTS`：`webapi`、`hybrid` 常用
- `STAGE3_ONLY_MODE`：`1` 代表僅執行階段三

## 啟動方式（依目前專案架構）

請分別開兩個終端機執行下列指令：

### 1) 啟動後端 API（FastAPI）

```bat
cmd.exe /c "cd /d c:\Users\dqaiot\Documents\aaron\gnerate_pictures && .venv\Scripts\python.exe -m uvicorn api.server:app --host 127.0.0.1 --port 8000"
```

### 2) 啟動前端（Vite）

```bat
cmd.exe /c "cd /d c:\Users\dqaiot\Documents\aaron\gnerate_pictures\frontend && npm run dev"
```

前端預設會呼叫 `http://127.0.0.1:8000`。

## API 端點

- `POST /upload-image`：上傳商品圖，伺服器會儲存為 `sample.jpg`
- `POST /run`：啟動流程（可帶 `user_input`、`stage3_only`）
- `GET /images/{filename}`：讀取輸出圖片

## CLI 用法（可選）

完整執行三階段：

```bat
cmd.exe /c "cd /d c:\Users\dqaiot\Documents\aaron\gnerate_pictures && .venv\Scripts\python.exe main.py"
```

只執行階段三：

```bat
cmd.exe /c "cd /d c:\Users\dqaiot\Documents\aaron\gnerate_pictures && .venv\Scripts\python.exe main.py --stage3-only"
```

## 輸入與輸出

- 輸入：`sample.jpg` + 使用者文字/網址
- 輸出：
  - `final_output.json`（P1～P9 結構化內容）
  - `picture\`（`P01_...png` 到 `P09_...png`）

## 注意事項

- 若缺少 `sample.jpg`，流程可能會中止
- 請勿提交含敏感資訊的 `.env`
- 建議先啟動後端再啟動前端
