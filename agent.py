import os
import json
import re
import requests
from bs4 import BeautifulSoup
import google.generativeai as genai
from PIL import Image
from duckduckgo_search import DDGS

# 引入我們定義好的 prompt template
from pormpt import prompt_template

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

def _extract_json_candidate(text: str) -> str:
    """
    從模型回覆中提取最可能的 JSON 陣列字串。
    """
    cleaned = text.strip()
    if cleaned.startswith("[") and cleaned.endswith("]"):
        return cleaned

    match = re.search(r"\[\s*\{[\s\S]*\}\s*\]", cleaned)
    if match:
        return match.group(0)
    return cleaned

def _validate_output(data: object) -> tuple[bool, str]:
    if not isinstance(data, list):
        return False, "根節點必須是陣列。"
    if len(data) != 9:
        return False, "陣列長度必須為 9（P1~P9）。"

    for i, item in enumerate(data, start=1):
        if not isinstance(item, dict):
            return False, f"第 {i} 筆不是物件。"

        required_keys = {"main", "畫面描述", "圖中文字", "規格", "sort"}
        if set(item.keys()) != required_keys:
            return False, f"第 {i} 筆 key 不符合固定結構。"

        if item["main"] != EXPECTED_MAINS[i - 1]:
            return False, f"第 {i} 筆 main 不正確。"
        if item["sort"] != i:
            return False, f"第 {i} 筆 sort 不正確。"
        if not isinstance(item["畫面描述"], str) or not item["畫面描述"].strip():
            return False, f"第 {i} 筆 畫面描述 必須是非空字串。"
        if not isinstance(item["規格"], str) or not item["規格"].strip():
            return False, f"第 {i} 筆 規格 必須是非空字串。"

        copy_block = item["圖中文字"]
        if not isinstance(copy_block, dict):
            return False, f"第 {i} 筆 圖中文字 必須是物件。"
        copy_required = {"主標", "副標", "標籤"}
        if set(copy_block.keys()) != copy_required:
            return False, f"第 {i} 筆 圖中文字 key 不符合固定結構。"
        if not isinstance(copy_block["主標"], str) or not copy_block["主標"].strip():
            return False, f"第 {i} 筆 主標 必須是非空字串。"
        if not isinstance(copy_block["副標"], str) or not copy_block["副標"].strip():
            return False, f"第 {i} 筆 副標 必須是非空字串。"
        if not isinstance(copy_block["標籤"], list) or len(copy_block["標籤"]) < 1:
            return False, f"第 {i} 筆 標籤 必須是非空陣列。"
        if not all(isinstance(tag, str) and tag.strip() for tag in copy_block["標籤"]):
            return False, f"第 {i} 筆 標籤 內容必須皆為非空字串。"

    return True, ""

def _repair_to_json(repair_model: genai.GenerativeModel, raw_text: str) -> list[dict]:
    """
    若模型輸出混入說明文字或格式錯誤，透過二次修復生成合法 JSON。
    """
    repair_prompt = f"""
請把以下內容修復成「合法 JSON」，並且只輸出 JSON 陣列本體，不要有任何額外文字。
必須符合：
1. 根節點是長度 9 的陣列（P1~P9）。
2. 每筆固定 key：main, 畫面描述, 圖中文字, 規格, sort。
3. 圖中文字固定 key：主標, 副標, 標籤。
4. main 與 sort 必須是既定順序與固定值。

以下是待修復原文：
{raw_text}
"""
    repaired = repair_model.generate_content(
        repair_prompt,
        generation_config={"temperature": 0.0},
    )
    candidate = _extract_json_candidate(repaired.text or "")
    data = json.loads(candidate)
    return data

def load_env_file(env_path: str = ".env") -> None:
    """
    載入 .env 檔案中的 KEY=VALUE 到環境變數（不覆蓋既有系統環境變數）。
    """
    if not os.path.exists(env_path):
        return

    with open(env_path, "r", encoding="utf-8") as f:
        for raw_line in f:
            line = raw_line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue

            key, value = line.split("=", 1)
            key = key.strip()
            value = value.strip().strip('"').strip("'")
            if key and key not in os.environ:
                os.environ[key] = value

def setup_gemini_api_key() -> bool:
    """
    讀取並設定 Gemini API 金鑰。
    支援 GOOGLE_API_KEY 與 GEMINI_API_KEY，優先使用 GOOGLE_API_KEY。
    """
    load_env_file()
    api_key = os.environ.get("GOOGLE_API_KEY") or os.environ.get("GEMINI_API_KEY")

    if not api_key:
        print("找不到 Gemini API 金鑰。")
        print("請在 .env 設定 GOOGLE_API_KEY 或 GEMINI_API_KEY。")
        print("範例：GOOGLE_API_KEY=你的金鑰")
        return False

    genai.configure(api_key=api_key)
    return True

# 2. 定義工具一：網路搜尋
def search_web(query: str) -> str:
    """
    使用網路搜尋引擎尋找最新資訊。
    當你需要查詢未知的即時資訊、新聞或市價時，請使用此工具。
    """
    print(f"\n👉 [系統提示] 觸發搜尋工具，關鍵字: {query}")
    try:
        results = DDGS().text(query, max_results=3)
        return str(results)
    except Exception as e:
        return f"搜尋失敗: {e}"

# 3. 定義工具二：讀取特定網頁內容
def fetch_webpage(url: str) -> str:
    """
    當使用者提供特定的網址 (URL) 時，使用此工具來讀取該網頁的文字內容。
    """
    print(f"\n👉 [系統提示] 觸發網頁讀取工具，正在訪問網址: {url}")
    try:
        headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64)'}
        response = requests.get(url, headers=headers, timeout=10)
        response.raise_for_status()
        soup = BeautifulSoup(response.text, 'html.parser')
        text = soup.get_text(separator='\n', strip=True)
        return text[:5000]
    except Exception as e:
        return f"無法讀取網頁: {e}"

def main():
    if not setup_gemini_api_key():
        return

    # 4. 準備圖片
    image_path = 'sample.jpg'
    try:
        image = Image.open(image_path)
    except FileNotFoundError:
        print(f"請準備一張名為 {image_path} 的圖片放在同目錄下。")
        return

    # 5. 獲取用戶輸入 (文字描述或 URL)
    print("歡迎使用 AI 電商圖文生成助手！")
    user_input = input("請輸入商品描述或相關網址 (例如 https://www.apple.com/tw/mac/)：\n> ")

    # ==========================================
    # 階段一：資訊收集 (Information Gathering)
    # ==========================================
    print("\n[階段一] 正在分析圖片與聯網收集商品資訊，請稍候...")
    info_model = genai.GenerativeModel(
        model_name='gemma-4-31b-it',
        tools=[search_web, fetch_webpage]
    )
    info_chat = info_model.start_chat(enable_automatic_function_calling=True)

    info_prompt = f"""
    請分析我上傳的商品圖片，並結合以下用戶提供的文字或網址資訊：
    「{user_input}」

    請利用你的搜尋與網頁讀取工具，幫我詳細總結這個商品的：
    1. 具體型號與基本介紹
    2. 行業屬性
    3. 目標客群
    4. 核心賣點
    5. 市場痛點

    請盡可能詳細且準確地列出這些資訊，這將作為後續行銷文案的基礎。
    """

    info_response = info_chat.send_message([info_prompt, image])
    gathered_info = info_response.text

    print("\n✅ [階段一完成] 收集到的商品資訊如下：")
    print("-" * 40)
    print(gathered_info)
    print("-" * 40)

    # ==========================================
    # 階段二：格式化輸出 (Formatting Output)
    # ==========================================
    print("\n[階段二] 正在結合 pormpt.py 規範，生成最終的 AI 繪圖提示詞與文案...")

    # 使用 system_instruction 載入 pormpt.py 中的嚴格規範
    format_model = genai.GenerativeModel(
        model_name='gemma-4-31b-it',
        system_instruction=prompt_template
    )
    repair_model = genai.GenerativeModel(
        model_name='gemma-4-31b-it',
        system_instruction=prompt_template
    )

    format_prompt = f"""
    請根據以下我為你收集好的商品資訊，以及我上傳的商品圖片，
    嚴格按照你的系統提示詞（System Instruction）中的固定格式（P1~P9），
    生成完整的電商商品視覺設計與行銷文案。

    【商品資訊】
    {gathered_info}
    """

    # 這裡不需要工具，純粹做文字與圖片的格式化生成
    format_response = format_model.generate_content(
        [format_prompt, image],
        generation_config={
            "temperature": 0.0,
            "response_mime_type": "application/json",
        },
    )

    raw_output = format_response.text or ""
    candidate = _extract_json_candidate(raw_output)

    final_data = None
    try:
        parsed = json.loads(candidate)
        ok, reason = _validate_output(parsed)
        if ok:
            final_data = parsed
        else:
            print(f"\n⚠️ [格式檢查] 首次輸出未通過：{reason}")
    except Exception as e:
        print(f"\n⚠️ [格式檢查] 首次輸出非合法 JSON：{e}")

    if final_data is None:
        print("[修復流程] 正在嘗試二次修復輸出格式...")
        repaired = _repair_to_json(repair_model, raw_output)
        ok, reason = _validate_output(repaired)
        if not ok:
            raise ValueError(f"修復後仍不符合 JSON 規範：{reason}")
        final_data = repaired

    print("\n🤖 [最終輸出] JSON:")
    print("=" * 60)
    print(json.dumps(final_data, ensure_ascii=False, indent=2))
    print("=" * 60)

if __name__ == "__main__":
    main()
