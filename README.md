## 專案簡介

`gnerate_pictures` 是一個用 **Gemini** 自動產生「電商商品九宮格（P1～P9）」的工具：

- **階段一**：讀取商品參考圖（預設 `sample.jpg`）+ 使用者輸入（文字或網址），蒐集商品資訊
- **階段二**：依 `pormpt.py` 的嚴格 JSON 規範，輸出 9 筆（P1～P9）的「場景描述 + 文案 + 規格」
- **階段三**：依 `picture.py` 的統一風格模板，將每一筆 P1～P9 轉為圖片 prompt，呼叫 Gemini 生成圖片並輸出到 `picture\`

> 主要入口程式是 `agent.py`。

---

## 專案結構

- **`agent.py`**：主程式（pipeline + 後端切換 + 輸出 JSON/圖片）
- **`pormpt.py`**：階段二用的「JSON 輸出格式」系統提示詞（強制 P1～P9、固定欄位、繁中）
- **`picture.py`**：階段三用的「圖片風格約束」模板（品牌色、寫實商業攝影、禁止某些文字等）
- **`sample.jpg`**：預設商品參考圖（階段一/二會讀）
- **`final_output.json`**：階段二的最終輸出（也可被「僅階段三」模式直接讀取）
- **`picture\`**：階段三輸出資料夾（產生 `P01_...png` 等）
- **`reference\`**：參考素材（範例 webp/mp4，供你自行比對或當資料來源）
- **`.env` / `.env.example`**：環境變數（後端/金鑰/Cookie/模式開關）

---

## 安裝與環境

### Python 依賴

依賴定義在 `requirements.txt`，包含：

- `google-genai`：API Key 模式的文字/圖片生成
- `gemini_webapi`：Cookie 模式（Gemini 網頁版）生成（專案直接 import 使用）
- `requests` + `beautifulsoup4`：網址內容抓取（階段一）
- `ddgs`：搜尋（階段一）
- `Pillow`：圖片讀寫/縮放
- `rembg[cpu]`：已預留去背能力（目前程式內是註解狀態）

### 建立虛擬環境（Windows）

```bat
cmd.exe /c "cd /d C:\Users\dqaiot\Documents\aaron\gnerate_pictures && py -m venv .venv && .venv\Scripts\python.exe -m pip install -r requirements.txt"
```

---

## 設定（.env）

請複製 `.env.example` 成 `.env` 後填入真實值（**不要提交含真實 KEY/Cookie 的 `.env`**）。

### 重要環境變數

- **`GEMINI_BACKEND`**：後端模式
  - **`apikey`**：全程使用 API Key（`google-genai`）
  - **`webapi`**：全程使用 Cookie（`gemini_webapi`）
  - **`hybrid`**：階段一/二用 API Key；階段三產圖用 Cookie
- **`GOOGLE_API_KEY`**（或 `GEMINI_API_KEY`）：API Key（`apikey` 或 `hybrid` 階段一/二必填）
- **`GEMINI_COOKIE_1PSID`**、**`GEMINI_COOKIE_1PSIDTS`**：Gemini 網頁版 Cookie（`webapi` 必填；`hybrid` 階段三必填）
- **`STAGE3_ONLY_MODE`**：`1` 時只跑階段三（直接讀 `final_output.json`）
- **`USE_EXISTING_IMAGE_ONLY`**：`1` 時階段三不呼叫 Gemini，改用既有 `generated_image.png` 當作測試輸出來源

---

## 使用方式

### 1) 完整流程（階段一～三）

1. 將商品參考圖放到專案根目錄並命名為 **`sample.jpg`**
2. 執行 `agent.py`，輸入商品描述或網址

```bat
cmd.exe /c "cd /d C:\Users\dqaiot\Documents\aaron\gnerate_pictures && .venv\Scripts\python.exe agent.py"
```

流程會依序：

- 印出階段一蒐集到的商品資訊
- 產出並儲存 **`final_output.json`**
- 產生圖片並輸出到 **`picture\`**

### 2) 只跑階段三（用既有 JSON 產圖）

當你已經有 `final_output.json`（而且格式正確、長度 9、P1～P9 鍵固定）：

```bat
cmd.exe /c "cd /d C:\Users\dqaiot\Documents\aaron\gnerate_pictures && .venv\Scripts\python.exe agent.py --stage3-only"
```

或在 `.env` 設定 `STAGE3_ONLY_MODE=1`。

### 3) 測試模式（不呼叫 Gemini，用既有圖輸出）

把一張圖放在根目錄並命名為 `generated_image.png`，然後：

```bat
cmd.exe /c "cd /d C:\Users\dqaiot\Documents\aaron\gnerate_pictures && .venv\Scripts\python.exe agent.py --stage3-only --use-existing-image"
```

---

## 輸入/輸出格式

### 輸入

- **參考圖**：`sample.jpg`（階段一/二需要；階段三也會用於 webapi 上傳檔案）
- **文字或網址**：執行後由互動式輸入提供

### 階段二輸出（`final_output.json`）

是一個長度為 9 的 JSON 陣列，每筆固定結構：

- `sort`：1～9
- `main`：固定為 P1～P9 的指定字串
- `scene`：具體畫面描述
- `copy`：`headline` / `subline` / `tags[]`
- `specs`：攝影/設計規格

（詳細格式定義見 `pormpt.py`）

### 階段三輸出（`picture\`）

- 產出檔名格式：`P01_<main>....png`
- 會統一縮放為 **1000×1000 PNG**

---

## 後端模式與行為差異（重點）

### `apikey`

- 階段一：由程式處理（網址抓取/搜尋）並把整理後資訊交給模型
- 階段二：用 `google-genai` 產出 JSON（並做格式驗證 + 必要時二次修復）
- 階段三：用 `google-genai` 圖片模型產圖（含 429/暫時中止的重試等待）

### `webapi`

- 階段一：直接讓 Gemini 網頁版自行搜尋；若使用者輸入含網址，程式會加上「必須瀏覽」的強制規則
- 階段二：用 `gemini_webapi` 產出 JSON（並做格式驗證 + 必要時二次修復）
- 階段三：用 `gemini_webapi` 產圖，並把檔案存到 `picture\`

### `hybrid`

- 階段一/二：API Key（`google-genai`）
- 階段三：Cookie（`gemini_webapi`）

---

## 已知限制 / 注意事項

- **目前階段三的迴圈在產出第一張圖後會 `break`**，因此預設只會產出 P1（這看起來像是除錯用行為）。如果你要一次產出 P1～P9，需要移除 `agent.py` 內階段三迴圈末尾的 `break`。
- **請勿提交 `.env`**：內含 API Key / Cookie 風險極高（專案已在 `.gitignore` 排除）。
- **圖片文字限制**：`picture.py` 內有「禁止某些文字出現在圖片上」的全域約束，產圖品質/合規會受此影響。

---

## 常見問題

### 找不到 `sample.jpg`

請把商品參考圖放在專案根目錄，並命名為 `sample.jpg`（或自行修改 `agent.py` 內預設檔名）。

### `final_output.json` 格式不正確

程式會用 `_validate_output()` 驗證長度與 key 結構；若模型輸出夾雜文字，會嘗試二次修復。若仍失敗，通常代表輸出沒有遵守 `pormpt.py` 的強制規範。
