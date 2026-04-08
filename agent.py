import os
import json
import re
import time
import sys
import io
import requests
from bs4 import BeautifulSoup
import google.genai as genai
from google.genai import types
from PIL import Image
from ddgs import DDGS
# from rembg import remove as rembg_remove

from pormpt import prompt_template
from picture import prompt_template as picture_style_template

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

TEXT_MODEL  = "gemini-3-flash-preview"
IMAGE_MODEL = "gemini-3.1-flash-image-preview"


def _extract_json_candidate(text: str) -> str:
    """從模型回覆中提取最可能的 JSON 陣列字串。"""
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

        required_keys = {"main", "scene", "copy", "specs", "sort"}
        if set(item.keys()) != required_keys:
            return False, f"第 {i} 筆 key 不符合固定結構。"

        if item["main"] != EXPECTED_MAINS[i - 1]:
            return False, f"第 {i} 筆 main 不正確。"
        if item["sort"] != i:
            return False, f"第 {i} 筆 sort 不正確。"
        if not isinstance(item["scene"], str) or not item["scene"].strip():
            return False, f"第 {i} 筆 scene 必須是非空字串。"
        if not isinstance(item["specs"], str) or not item["specs"].strip():
            return False, f"第 {i} 筆 specs 必須是非空字串。"

        copy_block = item["copy"]
        if not isinstance(copy_block, dict):
            return False, f"第 {i} 筆 copy 必須是物件。"
        copy_required = {"headline", "subline", "tags"}
        if set(copy_block.keys()) != copy_required:
            return False, f"第 {i} 筆 copy key 不符合固定結構。"
        if not isinstance(copy_block["headline"], str) or not copy_block["headline"].strip():
            return False, f"第 {i} 筆 headline 必須是非空字串。"
        if not isinstance(copy_block["subline"], str) or not copy_block["subline"].strip():
            return False, f"第 {i} 筆 subline 必須是非空字串。"
        if not isinstance(copy_block["tags"], list) or len(copy_block["tags"]) < 1:
            return False, f"第 {i} 筆 tags 必須是非空陣列。"
        if not all(isinstance(tag, str) and tag.strip() for tag in copy_block["tags"]):
            return False, f"第 {i} 筆 tags 內容必須皆為非空字串。"

    return True, ""


def _repair_to_json(client: genai.Client, raw_text: str) -> list[dict]:
    """若模型輸出混入說明文字或格式錯誤，透過二次修復生成合法 JSON。"""
    repair_prompt = f"""
請把以下內容修復成「合法 JSON」，並且只輸出 JSON 陣列本體，不要有任何額外文字。
必須符合：
1. 根節點是長度 9 的陣列（P1~P9）。
2. 每筆固定 key：sort, main, scene, copy, specs。
3. copy 固定 key：headline, subline, tags。
4. main 與 sort 必須是既定順序與固定值。

以下是待修復原文：
{raw_text}
"""
    repaired = client.models.generate_content(
        model=TEXT_MODEL,
        contents=repair_prompt,
        config=types.GenerateContentConfig(
            system_instruction=prompt_template,
            temperature=0.0,
        ),
    )
    candidate = _extract_json_candidate(repaired.text or "")
    data = json.loads(candidate)
    return data


def load_env_file(env_path: str = ".env") -> None:
    """載入 .env 檔案中的 KEY=VALUE 到環境變數（不覆蓋既有系統環境變數）。"""
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


def _generate_image_with_retry(
    client: genai.Client,
    image_prompt: str,
    product_image: Image.Image,
    max_retries: int = 5,
) -> object:
    """
    呼叫 Gemini 圖片生成，遇到 429 限速時自動讀取 retryDelay 並等待後重試。
    超過 max_retries 次仍失敗則拋出例外。
    product_image 為原始商品圖，讓模型保留外觀忠實度。
    """
    for attempt in range(max_retries + 1):
        try:
            return client.models.generate_content(
                model=IMAGE_MODEL,
                contents=[image_prompt, product_image],
                config=types.GenerateContentConfig(
                    response_modalities=["IMAGE"],
                    image_config=types.ImageConfig(
                        aspect_ratio="1:1",
                        image_size="512",
                    ),
                ),
            )
        except Exception as exc:
            err_str = str(exc)
            is_rate_limit = "429" in err_str or "RESOURCE_EXHAUSTED" in err_str
            if is_rate_limit and attempt < max_retries:
                # 從錯誤訊息中解析建議等待秒數，找不到則預設 60 秒
                delay_match = re.search(r"retryDelay['\"]?\s*[:\s]+['\"]?(\d+)s", err_str)
                wait_sec = int(delay_match.group(1)) + 5 if delay_match else 60
                print(
                    f"  ⏳ 遇到限速（429），等待 {wait_sec} 秒後重試"
                    f"（第 {attempt + 1}/{max_retries} 次）..."
                )
                time.sleep(wait_sec)
            else:
                raise


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


def fetch_webpage(url: str) -> str:
    """
    讀取指定網址的網頁文字內容。
    當使用者訊息中含有 http(s) 網址時必須呼叫本工具（每個相關網址至少一次），
    不可僅以搜尋結果推測該頁內容。
    """
    print(f"\n👉 [系統提示] 觸發網頁讀取工具，正在訪問網址: {url}")
    try:
        headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}
        response = requests.get(url, headers=headers, timeout=10)
        response.raise_for_status()
        soup = BeautifulSoup(response.text, "html.parser")
        text = soup.get_text(separator="\n", strip=True)
        print(text[:5000])
        return text[:5000]
    except Exception as e:
        return f"無法讀取網頁: {e}"


def main():
    load_env_file()
    stage3_only_mode = (
        os.environ.get("STAGE3_ONLY_MODE", "").lower() in {"1", "true", "yes"}
        or "--stage3-only" in sys.argv
    )
    client = None
    image = None
    final_data = None

    if not stage3_only_mode:
        api_key = os.environ.get("GOOGLE_API_KEY") or os.environ.get("GEMINI_API_KEY")
        if not api_key:
            return

        # 統一使用新 SDK Client，三個階段共用同一個 client 實例
        client = genai.Client(api_key=api_key)

        # 準備商品圖片
        image_path = "EX-11419WH-01.jpg"
        try:
            image = Image.open(image_path)
            image.load()
        except FileNotFoundError:
            print(f"請準備一張名為 {image_path} 的圖片放在同目錄下。")
            return

    print("歡迎使用 AI 電商圖文生成助手！")
    user_input = ""
    if not stage3_only_mode:
        user_input = input("請輸入商品描述或相關網址 (例如 https://www.apple.com/tw/mac/)：\n> ")

    # ==========================================
    # 階段一：資訊收集 (Information Gathering)
    # ==========================================
    if not stage3_only_mode:
        print("\n[階段一] 正在分析圖片與聯網收集商品資訊，請稍候...")

        info_chat = client.chats.create(
            model=TEXT_MODEL,
            config=types.GenerateContentConfig(
                tools=[search_web, fetch_webpage],
                automatic_function_calling=types.AutomaticFunctionCallingConfig(
                    disable=False,
                    maximum_remote_calls=10,
                ),
            ),
        )

        # 使用者若貼上網址，強制要求模型呼叫 fetch_webpage（不可只靠搜尋或臆測）
        _url_re = re.compile(r"https?://[^\s<>'\"\]\)]+", re.IGNORECASE)
        _dedup_urls: list[str] = []
        _seen_urls: set[str] = set()
        for _m in _url_re.finditer(user_input or ""):
            u = _m.group(0).rstrip(".,);]\"'")
            if u and u not in _seen_urls:
                _seen_urls.add(u)
                _dedup_urls.append(u)
        if _dedup_urls:
            url_mandatory_block = f"""
        【強制工具】使用者輸入含下列網址，你必須使用 fetch_webpage 工具逐一讀取（每個網址至少成功呼叫一次），
        並以網頁實際文字內容作為該段資訊的主要依據；禁止僅用 search_web 代替讀取該頁、禁止在未呼叫 fetch_webpage 前推測該網址上的規格或文案。
        網址清單：{"、".join(_dedup_urls)}
        """
        else:
            url_mandatory_block = """
        【網址規則】若使用者訊息中出現 http(s) 網址，必須使用 fetch_webpage 讀取該頁；不得以搜尋結果猜測該連結內容。
        """

        info_prompt = f"""
        請仔細分析我上傳的商品圖片，並結合以下用戶提供的文字或網址資訊：
        「{user_input}」
        {url_mandatory_block}
        請主動使用搜尋（search_web）與網頁讀取（fetch_webpage）工具查詢相關資料；有網址時 fetch_webpage 為必要步驟。
        依照以下結構逐項整理，這些資訊將直接用於後續生成 9 張電商商品圖的 AI 繪圖提示詞（P1～P9）：

        【基本資訊】
        - 商品品牌、完整型號、定價區間（台灣市場）
        - 商品品類與主要功能說明

        【P1 首圖素材】（CTR 點擊率優化）
        - 商品最強、最有辨識度的差異化賣點（1～3 點，需具體）
        - 適合作為視覺主角的商品外觀特色

        【P2 痛點素材】
        - 目標客群使用此類商品前最常遇到的 2～3 個具體痛點或挫折情境
        - 使用本商品後能解決哪些問題

        【P3 解決方案素材】
        - 商品核心技術、專利工法或關鍵成分名稱
        - 這些技術帶來的實際可感知效益

        【P4 日常場景素材】
        - 台灣用戶最高頻使用此商品的日常場景（時間、地點、情境）
        - 此場景下商品最突出的表現

        【P5 極限場景素材】
        - 此商品適合的高需求或特殊使用情境（戶外/高負荷/惡劣環境等）
        - 極限條件下的性能數據或表現優勢

        【P6 細節品質素材】
        - 商品最能展現品質的材質、工藝或製造細節
        - 相關品質認證、耐用度數據

        【P7 競品對比素材】
        - 台灣市場上的主要競品品牌與型號
        - 本商品相較競品在哪 1～2 個關鍵指標上明顯佔優（最好有數據）

        【P8 附加功能素材】
        - 商品的人性化設計、附加功能、贈品或特殊包裝
        - 這些設計帶來的使用便利性

        【P9 規格素材】
        - 完整規格參數（尺寸、重量、材質、適用範圍、認證規格等）
        - 適用對象或使用條件說明

        請盡可能查詢最新且準確的台灣在地資訊，若搜尋不到特定資料請標注「待確認」。
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
    if not stage3_only_mode:
        print("\n[階段二] 正在結合 pormpt.py 規範，生成最終的 AI 繪圖提示詞與文案...")

        format_prompt = f"""
    請根據以下我為你收集好的商品資訊，以及我上傳的商品圖片，
    嚴格按照你的系統提示詞（System Instruction）中的固定格式（P1~P9），
    生成完整的電商商品視覺設計與行銷文案。

    【商品資訊】
    {gathered_info}
    """

        format_response = client.models.generate_content(
            model=TEXT_MODEL,
            contents=[format_prompt, image],
            config=types.GenerateContentConfig(
                system_instruction=prompt_template,
                temperature=0.0,
                response_mime_type="application/json",
            ),
        )

        raw_output = format_response.text or ""
        candidate = _extract_json_candidate(raw_output)

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
            repaired = _repair_to_json(client, raw_output)
            ok, reason = _validate_output(repaired)
            if not ok:
                raise ValueError(f"修復後仍不符合 JSON 規範：{reason}")
            final_data = repaired

        print("\n🤖 [最終輸出] JSON:")
        print("=" * 60)
        print(json.dumps(final_data, ensure_ascii=False, indent=2))
        print("=" * 60)

        output_json_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "final_output.json")
        with open(output_json_path, "w", encoding="utf-8") as f:
            json.dump(final_data, f, ensure_ascii=False, indent=2)
        print(f"💾 [JSON 已儲存] {output_json_path}")
    else:
        output_json_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "final_output.json")
        if not os.path.exists(output_json_path):
            print(f"找不到 {output_json_path}，無法執行僅階段三模式。")
            return
        with open(output_json_path, "r", encoding="utf-8") as f:
            final_data = json.load(f)
        ok, reason = _validate_output(final_data)
        if not ok:
            print(f"final_output.json 格式不正確：{reason}")
            return
        print(f"\n🧪 已啟用僅階段三模式，直接使用 {output_json_path}")

    use_existing_image_only = (
        os.environ.get("USE_EXISTING_IMAGE_ONLY", "").lower() in {"1", "true", "yes"}
        or "--use-existing-image" in sys.argv
    )
    # 僅階段三模式略過前兩階段，未建立 client / 商品圖；實際呼叫圖片 API 時仍需要兩者
    if not use_existing_image_only:
        if client is None:
            api_key = os.environ.get("GOOGLE_API_KEY") or os.environ.get("GEMINI_API_KEY")
            if not api_key:
                print("請在環境變數或 .env 設定 GOOGLE_API_KEY 或 GEMINI_API_KEY，否則無法生成圖片。")
                return
            client = genai.Client(api_key=api_key)
        if image is None:
            image_path = "EX-11419WH-01.jpg"
            try:
                image = Image.open(image_path)
                image.load()
            except FileNotFoundError:
                print(
                    f"階段三需要商品參考圖，請將 {image_path} 放在程式同目錄，"
                    "或先關閉僅階段三模式跑完整流程。"
                )
                return

    # ==========================================
    # 階段三：圖片生成 (Image Generation)
    # ==========================================
    print("\n[階段三] 正在為每張圖生成 AI 圖片，請稍候...")

    picture_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "picture")
    os.makedirs(picture_dir, exist_ok=True)
    existing_image_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "generated_image.png")

    for item in final_data:
        sort_num   = item["sort"]
        main_name  = item["main"]
        scene      = item["scene"]
        copy_block = item["copy"]
        headline   = copy_block["headline"]
        subline    = copy_block["subline"]
        tags       = copy_block["tags"]
        specs      = item["specs"]

        tags_str = "、".join(tags)

        # 將 JSON 欄位轉換成 pormpt.py 的文字描述格式，並套用 picture.py 視覺風格約束
        image_prompt = f"""{picture_style_template}

### {main_name}
- scene：{scene}
- headline：{headline}
- subline：{subline}
- tags：{tags_str}
- specs：{specs}
"""

        print(f"\n🎨 [P{sort_num:02d}] 正在生成：{main_name} ...")
        safe_name = (
            main_name
            .replace(" ", "_")
            .replace("/", "_")
            .replace("：", "")
        )

        try:
            print(image_prompt)
            raw_image = None
            if use_existing_image_only:
                if os.path.exists(existing_image_path):
                    raw_image = Image.open(existing_image_path)
                    raw_image.load()
                    print(f"  ℹ️  測試模式：使用既有圖片 {existing_image_path}")
                else:
                    print(f"  ⚠️  測試模式啟用，但找不到檔案：{existing_image_path}")
                    continue
            else:
                response = _generate_image_with_retry(client, image_prompt, image)
                for part in response.parts:
                    if part.inline_data is not None:
                        image_bytes = part.inline_data.data
                        raw_image = Image.open(io.BytesIO(image_bytes))
                        raw_image.load()
                        raw_image.save(f"P{sort_num:02d}_{safe_name}.png")
                        break

            if raw_image is None:
                print(f"  ⚠️  P{sort_num:02d} 未取得圖片內容，跳過。")
                continue

            # 統一縮放至 1000×1000
            resized = raw_image.resize((1000, 1000), Image.LANCZOS)

            # # 去背：Gemini 不原生支援透明背景，使用 rembg 移除背景輸出 RGBA PNG
            # no_bg = rembg_remove(resized)

            filename  = f"P{sort_num:02d}_{safe_name}.png"
            filepath  = os.path.join(picture_dir, filename)
            resized.save(filepath, "PNG")
            print(f"  ✅ 已儲存（1000×1000 去背 PNG）：{filepath}")

        except Exception as e:
            print(f"  ❌ P{sort_num:02d} 圖片生成失敗：{e}")

        break
        # 每張圖之間停頓，避免連續請求觸發限速
        # time.sleep(3)

    print("\n✅ [階段三完成] 所有圖片已儲存至 picture/ 資料夾。")


if __name__ == "__main__":
    main()