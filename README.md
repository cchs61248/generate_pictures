## 專案簡介

`gnerate_pictures` 是一個用 **Gemini** 自動產生「電商商品九宮格（P1～P9）」的工具：

- **階段一**：讀取商品參考圖（預設 `sample.jpg`）+ 使用者輸入（文字或網址），蒐集商品資訊
- **階段二**：依 `prompts\json_schema.py` 的 JSON 規範，輸出 9 筆（P1～P9）的「場景描述 + 文案 + 規格」
- **階段三**：依 `prompts\image_style.py` 的風格模板，將每一筆轉為圖片 prompt，呼叫 Gemini 產圖輸出到 `picture\`

目前主入口為 **`main.py`**。

---

## 專案結構（重構後）

- **`main.py`**：CLI 主入口（唯一 `asyncio.run`）
- **`core\config.py`**：環境變數載入、常數、模式解析
- **`core\clients.py`**：`apikey / webapi / hybrid` client 初始化
- **`core\pipeline.py`**：三階段協調器
- **`stages\stage1_gather.py`**：階段一（資訊收集）
- **`stages\stage2_json.py`**：階段二（JSON 生成 / 驗證 / 修復）
- **`stages\stage3_image.py`**：階段三（產圖 / 儲存）
- **`services\`**：搜尋、產圖重試、圖片處理
- **`utils\json_utils.py`**：JSON 抽取、驗證與修復
- **`prompts\json_schema.py`**：階段二 prompt
- **`prompts\image_style.py`**：階段三 prompt
- **`api\server.py`**：FastAPI 介面（前端串接）

---

## 安裝與環境

### 建立虛擬環境與安裝依賴（Windows）

```bat
cmd.exe /c "cd /d C:\Users\dqaiot\Documents\aaron\gnerate_pictures && py -m venv .venv && .venv\Scripts\python.exe -m pip install -r requirements.txt"
```

---

## 設定（.env）

請複製 `.env.example` 成 `.env` 後填入真實值（請勿提交含真實 KEY/Cookie 的 `.env`）。

### 重要環境變數

- `GEMINI_BACKEND`：`apikey` / `webapi` / `hybrid`
- `GOOGLE_API_KEY`（或 `GEMINI_API_KEY`）：`apikey` 或 `hybrid`（階段一/二）必填
- `GEMINI_COOKIE_1PSID`、`GEMINI_COOKIE_1PSIDTS`：`webapi` 或 `hybrid`（階段三）必填
- `STAGE3_ONLY_MODE`：`1` 時只跑階段三（讀取 `final_output.json`）

---

## 使用方式

### 1) 完整流程（階段一～三）

```bat
cmd.exe /c "cd /d C:\Users\dqaiot\Documents\aaron\gnerate_pictures && .venv\Scripts\python.exe main.py"
```

### 2) 只跑階段三（用既有 JSON 產圖）

```bat
cmd.exe /c "cd /d C:\Users\dqaiot\Documents\aaron\gnerate_pictures && .venv\Scripts\python.exe main.py --stage3-only"
```

## API（前端串接）

啟動 API 服務：

```bat
cmd.exe /c "cd /d C:\Users\dqaiot\Documents\aaron\gnerate_pictures && .venv\Scripts\python.exe -m uvicorn api.server:app --host 127.0.0.1 --port 8000"
```

### Endpoint

- `POST /run`：觸發 pipeline
  - body 可帶：`user_input`、`stage3_only`
- `GET /images/{filename}`：讀取輸出圖片

---

## 輸入 / 輸出

### 輸入

- 參考圖：`sample.jpg`
- 使用者輸入：CLI 互動輸入文字或網址（或 API 的 `user_input`）

### 輸出

- `final_output.json`：長度 9 的 P1～P9 結構化內容
- `picture\`：`P01_...png` 至 `P09_...png`（統一 1000x1000）

---

## 注意事項

- 階段三已支援完整跑完 P1～P9（不再只產第一張）
- 請勿提交 `.env`
- 若缺少 `sample.jpg`，流程會中止並提示
