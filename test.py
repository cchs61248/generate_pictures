import sys
import os
import json
import re
import time
import uuid
import requests
from urllib.parse import quote, urlencode

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

# ─────────────────────────────────────────────────────────────────────────────
# 設定
# ─────────────────────────────────────────────────────────────────────────────

IMAGE_PATH = "sample.jpg"

PROMPT = (
    "請幫我生成一張電商商品圖片，圖片大小1000*1000，請參考我上傳的商品圖片外觀，依照以下設計要求生成：\n\n\n"
    "## 統一風格：\n"
    "- **深藍 #003355 + 明黃 #FFD100 品牌色統一**\n"
    "- **寫實商業產品攝影**\n"
    "- **高解析、真實材質、自然金屬反光**\n"
    "- **台灣電商商品詳情頁風格**\n"
    "- **文字排版清晰，主標大、副標次之、標籤明黃高亮**\n"
    "- **背景乾淨，資訊層級明確**\n"
    "- **避免 AI 假感、避免誇張卡通化、避免過度抽象**\n"
    "- **產品外觀需忠實還原上傳圖片的造型、切割端、滾輪結構與整體比例**\n"
    "- **符合產品使用情境和物品的特性，不要臆測和誇大**\n"
    "*圖片上的文字嚴格禁止出現: 主標、副標、標籤 等文字*\n\n\n"
    "### P1 首圖 CTR核心 Prompt\n"
    "- scene：EXCELL ET-22606 切帶機置於畫面中央，呈 45 度側身展現全金屬藍色機身質感。"
    "背景採用深藍色與明黃色交織的幾何色塊，營造專業工業感。"
    "畫面右側加入一張纖維膠帶被俐落切斷的特寫，切口平整，伴隨輕微的動態模糊效果。"
    "光線聚焦在黃色導帶輪與專利安全護蓋上，強調視覺重心。\n"
    "- headline：專業封箱，安全不傷手\n"
    "- subline：專為強韌纖維膠帶設計，全金屬機身\n"
    "- tags：專利安全蓋、纖維膠帶專用、全金屬耐摔\n"
    "- specs：寫實商業攝影，強烈明暗對比，商品 45° 角俯拍，4:3 構圖，品牌色調 #003355 與 #FFD100 結合，CTR 優化。"
)

# ─────────────────────────────────────────────────────────────────────────────
# Cookies（從 cookies.json 讀取）
# ─────────────────────────────────────────────────────────────────────────────

_COOKIES_FILE = os.path.join(os.path.dirname(__file__), "cookies.json")

def _load_cookies(path: str) -> dict:
    with open(path, "r", encoding="utf-8") as f:
        entries = json.load(f)
    return {entry["name"]: entry["value"] for entry in entries}

cookies = _load_cookies(_COOKIES_FILE)

# ─────────────────────────────────────────────────────────────────────────────
# Headers（共用；content-type 依需求覆蓋）
# ─────────────────────────────────────────────────────────────────────────────

BASE_HEADERS = {
    "accept": "*/*",
    "accept-encoding": "gzip, deflate, br, zstd",
    "accept-language": "zh-TW,zh;q=0.9",
    "origin": "https://gemini.google.com",
    "priority": "u=1, i",
    "referer": "https://gemini.google.com/",
    "sec-ch-ua": '"Google Chrome";v="147", "Not.A/Brand";v="8", "Chromium";v="147"',
    "sec-ch-ua-arch": '"x86"',
    "sec-ch-ua-bitness": '"64"',
    "sec-ch-ua-form-factors": '"Desktop"',
    "sec-ch-ua-full-version": "147.0.7727.50",
    "sec-ch-ua-full-version-list": '"Google Chrome";v="147.0.7727.50", "Not.A/Brand";v="8.0.0.0", "Chromium";v="147.0.7727.50"',
    "sec-ch-ua-mobile": "?0",
    "sec-ch-ua-model": '""',
    "sec-ch-ua-platform": '"Windows"',
    "sec-ch-ua-platform-version": '"19.0.0"',
    "sec-ch-ua-wow64": "?0",
    "sec-fetch-dest": "empty",
    "sec-fetch-mode": "cors",
    "sec-fetch-site": "same-origin",
    "user-agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/147.0.0.0 Safari/537.36",
    "x-browser-channel": "stable",
    "x-browser-copyright": "Copyright 2026 Google LLC. All Rights reserved.",
    "x-browser-validation": "XWVhzN8UuawgNeo+/cd5rjMggcA=",
    "x-browser-year": "2026",
    "x-goog-ext-525001261-jspb": "[1,null,null,null,\"fbb127bbb056c959\",null,null,0,[4],null,null,1]",
    "x-goog-ext-525005358-jspb": "[\"F4781EDD-BB22-41BA-B0DB-CD81ABF1FDA3\",1]",
    "x-goog-ext-73010989-jspb": "[0]",
    "x-goog-ext-73010990-jspb": "[0]",
    "x-same-domain": "1",
}

# ─────────────────────────────────────────────────────────────────────────────
# Step 1：上傳圖片，取得 contrib_service token
# ─────────────────────────────────────────────────────────────────────────────

def upload_image(image_path: str, session: requests.Session) -> str:
    """
    上傳本地圖片至 Gemini contrib_service，
    回傳 token 路徑（形如 /contrib_service/ttl_1d/<token>）。
    """
    filename = os.path.basename(image_path)
    file_size = os.path.getsize(image_path)

    ext = os.path.splitext(filename)[1].lower()
    mime_map = {".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".png": "image/png", ".webp": "image/webp"}
    mime_type = mime_map.get(ext, "image/jpeg")

    print(f"[上傳] 圖片：{filename}，大小：{file_size} bytes，類型：{mime_type}")
    return _upload_via_bard(image_path, filename, mime_type, file_size, session)


def _upload_via_bard(image_path: str, filename: str, mime_type: str, file_size: int, session: requests.Session) -> str:
    """
    使用 push.clients6.google.com/upload/ 兩段式上傳圖片。
    回傳 /contrib_service/ttl_1d/<token> 格式的路徑。

    關鍵 headers（從瀏覽器抓包確認）：
      - push-id: feeds/mcudyrk2a4khkz  ← ClientId/Feed name，缺少會 400
      - x-tenant-id: bard-storage
      - x-client-pctx: CgcSBWjK7pYx
      - sec-fetch-site: same-site（非 same-origin）
      - x-goog-upload-header-content-type 不需要帶
    """
    UPLOAD_BASE = "https://push.clients6.google.com/upload/"

    # Phase 1：帶 "File name: <filename>" 取得可續傳 URL
    init_headers = {
        "accept": "*/*",
        "accept-encoding": "gzip, deflate, br, zstd",
        "accept-language": "zh-TW,zh;q=0.9",
        "content-type": "application/x-www-form-urlencoded;charset=UTF-8",
        "origin": "https://gemini.google.com",
        "priority": "u=1, i",
        "push-id": "feeds/mcudyrk2a4khkz",
        "referer": "https://gemini.google.com/",
        "sec-ch-ua": BASE_HEADERS["sec-ch-ua"],
        "sec-ch-ua-arch": BASE_HEADERS["sec-ch-ua-arch"],
        "sec-ch-ua-bitness": BASE_HEADERS["sec-ch-ua-bitness"],
        "sec-ch-ua-form-factors": BASE_HEADERS["sec-ch-ua-form-factors"],
        "sec-ch-ua-full-version": BASE_HEADERS["sec-ch-ua-full-version"],
        "sec-ch-ua-full-version-list": BASE_HEADERS["sec-ch-ua-full-version-list"],
        "sec-ch-ua-mobile": BASE_HEADERS["sec-ch-ua-mobile"],
        "sec-ch-ua-model": BASE_HEADERS["sec-ch-ua-model"],
        "sec-ch-ua-platform": BASE_HEADERS["sec-ch-ua-platform"],
        "sec-ch-ua-platform-version": BASE_HEADERS["sec-ch-ua-platform-version"],
        "sec-ch-ua-wow64": BASE_HEADERS["sec-ch-ua-wow64"],
        "sec-fetch-dest": "empty",
        "sec-fetch-mode": "cors",
        "sec-fetch-site": "same-site",
        "user-agent": BASE_HEADERS["user-agent"],
        "x-browser-channel": "stable",
        "x-browser-copyright": BASE_HEADERS["x-browser-copyright"],
        "x-browser-validation": BASE_HEADERS["x-browser-validation"],
        "x-browser-year": "2026",
        "x-client-pctx": "CgcSBWjK7pYx",
        "x-goog-upload-command": "start",
        "x-goog-upload-header-content-length": str(file_size),
        "x-goog-upload-protocol": "resumable",
        "x-tenant-id": "bard-storage",
    }
    init_body = f"File name: {filename}".encode()

    print("[上傳] Phase 1：初始化，取得 upload URL")
    init_resp = session.post(UPLOAD_BASE, headers=init_headers, data=init_body, timeout=30)
    if init_resp.status_code != 200:
        raise RuntimeError(
            f"[上傳] Phase 1 失敗，狀態碼：{init_resp.status_code}\n"
            f"  回應：{init_resp.text[:300]}"
        )

    upload_url = (
        init_resp.headers.get("x-goog-upload-url")
        or init_resp.headers.get("location", "")
    )
    if not upload_url:
        raise RuntimeError(
            f"[上傳] Phase 1 未取得 upload URL\n"
            f"  回應 headers：{dict(init_resp.headers)}\n"
            f"  回應 body：{init_resp.text[:300]}"
        )
    print(f"[上傳] 取得 upload URL：{upload_url[:100]}...")

    # Phase 2：上傳圖片 bytes
    upload_headers = {
        "accept": "*/*",
        "accept-encoding": "gzip, deflate, br, zstd",
        "content-type": mime_type,
        "origin": "https://gemini.google.com",
        "referer": "https://gemini.google.com/",
        "sec-fetch-dest": "empty",
        "sec-fetch-mode": "cors",
        "sec-fetch-site": "same-site",
        "user-agent": BASE_HEADERS["user-agent"],
        "x-goog-upload-command": "upload, finalize",
        "x-goog-upload-offset": "0",
    }
    with open(image_path, "rb") as f:
        file_data = f.read()

    print(f"[上傳] Phase 2：上傳 {len(file_data)} bytes")
    upload_resp = session.post(upload_url, headers=upload_headers, data=file_data, timeout=60)
    upload_resp.raise_for_status()

    token_path = upload_resp.text.strip()
    if not token_path.startswith("/contrib_service"):
        raise RuntimeError(f"[上傳] 回傳格式非預期：{token_path[:300]}")

    print(f"[上傳] 成功，token: {token_path[:80]}")
    return token_path


# ─────────────────────────────────────────────────────────────────────────────
# Step 2：組裝 f.req form_data
# ─────────────────────────────────────────────────────────────────────────────

def build_form_data(prompt: str, image_token: str, filename: str, at_token: str) -> str:
    """
    依照瀏覽器抓包精確格式組裝 f.req。

    解碼後結構：
    outer = [null, "<inner_str>"]
    inner_str = JSON.stringify([[
        [prompt, 0, null, [[[token,1,null,mime],filename,null*6,[0]]], null, null, 0],
        ["zh-TW"],
        ["","","",null,null,null,null,null,null,""],
        "<at_hash>", "<session_hash>",
        null,[1],1,null,null,1,0,null,...
    ]])
    """
    mime_type = "image/jpeg" if filename.lower().endswith((".jpg", ".jpeg")) else "image/png"
    session_uuid = "F4781EDD-BB22-41BA-B0DB-CD81ABF1FDA3"

    # 圖片附件：[[[token,1,null,mime], filename, null,null,null,null,null,null, [0]]]
    image_part = [
        [[image_token, 1, None, mime_type],
         filename,
         None, None, None, None, None, None, [0]]
    ]

    # inner_list 對應抓包解碼的完整陣列
    inner_list = [
        [prompt, 0, None, image_part, None, None, 0],
        ["zh-TW"],
        ["", "", "", None, None, None, None, None, None, ""],
        None, None, None,
        [1],
        1, None, None, 1, 0,
        None, None, None, None, None,
        [[0]],
        0,
        None, None, None, None, None, None, None, None,
        1, None, None,
        [4],
        None, None, None, None, None, None, None, None, None, None,
        [1],
        None, None, None, 1, None, None, None, None, None, None, None,
        0,
        None, None, None, None, None,
        session_uuid,
        None, [],
        None, None, None, None, None, None,
        2,
        None, None, None, None, None, None, None, None, None, None,
        1,
    ]

    # 原始格式：outer[1] = "[entry1, entry2, ...]"（平鋪陣列，非雙層 [[]]）
    inner_str = json.dumps(inner_list, ensure_ascii=False, separators=(",", ":"))

    # outer = [null, inner_str]，再整個 JSON.stringify
    outer = [None, inner_str]
    f_req_val = json.dumps(outer, ensure_ascii=False, separators=(",", ":"))

    params = {
        "f.req": f_req_val,
        "at": at_token,
    }
    return urlencode(params) + "&"


# ─────────────────────────────────────────────────────────────────────────────
# Step 3：解析回應，擷取生成圖片 URL
# ─────────────────────────────────────────────────────────────────────────────

def parse_stream_response(text: str) -> dict:
    """
    解析 Gemini StreamGenerate 串流回應。
    - text：最終完整文字回應
    - image_urls：生成圖片的 gg-dl URL 列表
    """
    result = {"text": "", "image_urls": []}

    body = text
    if body.startswith(")]}'"):
        body = body[4:]

    chunks = re.split(r'\n\d+\n', body)

    for raw in chunks:
        raw = raw.strip()
        if not raw.startswith("["):
            continue
        try:
            outer = json.loads(raw)
        except json.JSONDecodeError:
            continue
        if not isinstance(outer, list):
            continue

        for entry in outer:
            if not isinstance(entry, list) or len(entry) < 3:
                continue
            if entry[0] != "wrb.fr":
                continue
            inner_raw = entry[2]
            if not isinstance(inner_raw, str):
                continue
            try:
                inner = json.loads(inner_raw)
            except json.JSONDecodeError:
                continue

            inner_str = json.dumps(inner, ensure_ascii=False)

            # 文字回應：inner[4][0][1][0]
            try:
                text_part = inner[4][0][1][0]
                if isinstance(text_part, str) and text_part:
                    result["text"] = text_part
            except (IndexError, TypeError):
                pass

            # 生成圖片 URL：gg-dl 路徑（Imagen 生成結果）
            # 結構在 inner[4][0][12][0][0][0][0][3][3] 附近，直接用 regex 抓
            gen_urls = re.findall(
                r'https://lh3\.googleusercontent\.com/gg-dl/[^\s\'"\\,\]\[]+',
                inner_str,
            )
            result["image_urls"].extend(gen_urls)

            # 一般附有副檔名的 googleusercontent 連結（備用）
            other_urls = re.findall(
                r'https://lh3\.googleusercontent\.com/gg/[^\s\'"\\,\]\[]+',
                inner_str,
            )
            # gg/ 是上傳圖片的縮圖，不是生成結果，跳過
            _ = other_urls

    result["image_urls"] = list(dict.fromkeys(result["image_urls"]))
    return result


# ─────────────────────────────────────────────────────────────────────────────
# Step 4：下載圖片
# ─────────────────────────────────────────────────────────────────────────────

def download_image(img_url: str, save_path: str, session: requests.Session) -> bool:
    try:
        resp = session.get(img_url, timeout=60, stream=True)
        resp.raise_for_status()
        with open(save_path, "wb") as f:
            for chunk in resp.iter_content(chunk_size=8192):
                f.write(chunk)
        print(f"  [下載完成] {save_path}")
        return True
    except Exception as e:
        print(f"  [下載失敗] {img_url[:80]} → {e}")
        return False


# ─────────────────────────────────────────────────────────────────────────────
# 主流程
# ─────────────────────────────────────────────────────────────────────────────

# StreamGenerate URL（f.sid / _reqid 可視需要更新）
STREAM_URL = (
    "https://gemini.google.com/_/BardChatUi/data/assistant.lamda.BardFrontendService/"
    "StreamGenerate?bl=boq_assistant-bard-web-server_20260405.11_p3"
    "&f.sid=YOUR_GEMINI_F_SID_HERE&hl=zh-TW&_reqid=YOUR_GEMINI_REQID_HERE&rt=c"
)

# at token（從瀏覽器抓包取得，有效期短，需定期更新）
AT_TOKEN = "AJrheLV8pavm_1TSSqaPNGrwDBnN:1775638461005"

STREAM_HEADERS = {
    **BASE_HEADERS,
    "content-type": "application/x-www-form-urlencoded;charset=UTF-8",
}


def main():
    session = requests.Session()
    session.cookies.update(cookies)

    # ── 1. 上傳圖片 ───────────────────────────────────────────────────────
    print(f"[步驟 1] 上傳圖片：{IMAGE_PATH}")
    try:
        image_token = upload_image(IMAGE_PATH, session)
    except Exception as e:
        print(f"[錯誤] 圖片上傳失敗：{e}")
        return

    filename = os.path.basename(IMAGE_PATH)

    # ── 2. 組裝 form_data ─────────────────────────────────────────────────
    print("\n[步驟 2] 組裝請求")
    form_data = build_form_data(PROMPT, image_token, filename, AT_TOKEN)
    print(f"  form_data 長度：{len(form_data)} bytes")

    # ── 3. 發送 StreamGenerate 請求 ───────────────────────────────────────
    print(f"\n[步驟 3] POST → {STREAM_URL.split('?')[0]}")
    try:
        resp = session.post(
            STREAM_URL,
            headers=STREAM_HEADERS,
            data=form_data,
            timeout=120,
        )
        resp.raise_for_status()
    except requests.RequestException as e:
        print(f"[錯誤] 請求失敗：{e}")
        return

    print(f"  狀態碼：{resp.status_code}，回應長度：{len(resp.text)} bytes")

    # ── 4. 解析並下載 ─────────────────────────────────────────────────────
    print("\n[步驟 4] 解析回應")
    parsed = parse_stream_response(resp.text)

    if parsed["text"]:
        print(f"\n[文字回應]\n{parsed['text']}\n")

    if parsed["image_urls"]:
        print(f"[生成圖片] 共偵測到 {len(parsed['image_urls'])} 個 URL，僅下載 PNG：")
        png_urls = [u for u in parsed["image_urls"] if ".png" in u.lower()]
        if not png_urls:
            print("  [警告] 未找到 PNG URL，改下載全部")
            png_urls = parsed["image_urls"]
        for i, img_url in enumerate(png_urls, 1):
            print(f"  {i}. {img_url[:100]}...")
            download_image(img_url, f"generated_{i}.png", session)
    else:
        print("[生成圖片] 未偵測到生成圖片")


if __name__ == "__main__":
    main()
