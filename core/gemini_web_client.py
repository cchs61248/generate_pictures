"""
GeminiWebClient
===============
以手刻 HTTP 直連 gemini.google.com 的方式提供與 gemini_webapi.GeminiClient
相同的非同步介面，讓其餘程式碼可無痛替換，不需安裝 gemini_webapi 套件。

公開介面
--------
client = GeminiWebClient(cookies_path="cookies.json")
await client.init()
response = await client.generate_content(prompt, files=["/path/to/image.jpg"])
response.text          # str：文字回應
response.images        # list[GeneratedImage]：生成的圖片物件

GeneratedImage.data    # bytes：PNG 圖片原始 bytes
"""

from __future__ import annotations

import asyncio
import hashlib
import io
import json
import os
import re
import time
import uuid
from dataclasses import dataclass, field
from typing import Optional
from urllib.parse import urlencode

import requests
from PIL import Image


# ─────────────────────────────────────────────────────────────────────────────
# 常數
# ─────────────────────────────────────────────────────────────────────────────

_UPLOAD_BASE = "https://push.clients6.google.com/upload/"

_BASE_HEADERS_STATIC = {
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
    "sec-ch-ua-full-version-list": (
        '"Google Chrome";v="147.0.7727.50", "Not.A/Brand";v="8.0.0.0", "Chromium";v="147.0.7727.50"'
    ),
    "sec-ch-ua-mobile": "?0",
    "sec-ch-ua-model": '""',
    "sec-ch-ua-platform": '"Windows"',
    "sec-ch-ua-platform-version": '"19.0.0"',
    "sec-ch-ua-wow64": "?0",
    "sec-fetch-dest": "empty",
    "sec-fetch-mode": "cors",
    "sec-fetch-site": "same-origin",
    "user-agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/147.0.0.0 Safari/537.36"
    ),
    "x-browser-channel": "stable",
    "x-browser-copyright": "Copyright 2026 Google LLC. All Rights reserved.",
    "x-browser-validation": "XWVhzN8UuawgNeo+/cd5rjMggcA=",
    "x-browser-year": "2026",
    "x-goog-ext-73010989-jspb": "[0]",
    "x-goog-ext-73010990-jspb": "[0]",
    "x-same-domain": "1",
}

_STREAM_URL_TEMPLATE = (
    "https://gemini.google.com/_/BardChatUi/data/assistant.lamda.BardFrontendService/"
    "StreamGenerate?bl=boq_assistant-bard-web-server_20260405.11_p3"
    "&f.sid={f_sid}&hl=zh-TW&_reqid={reqid}&rt=c"
)


# ─────────────────────────────────────────────────────────────────────────────
# 工具函式：從 cookies 動態計算 headers
# ─────────────────────────────────────────────────────────────────────────────

def _sapisidhash(sapisid: str) -> str:
    """計算 SAPISIDHASH = SHA1(ts + ' ' + SAPISID)，取前16 hex 字元。"""
    ts = str(int(time.time()))
    digest = hashlib.sha1(f"{ts} {sapisid}".encode()).hexdigest()
    return digest[:16]


def _build_dynamic_headers(cookies: dict, session_uuid: str) -> dict:
    """
    根據當前 cookies 動態產生需要每次更新的 headers。

    - x-goog-ext-525001261-jspb：含 SAPISIDHASH，由 SAPISID cookie 計算
    - x-goog-ext-525005358-jspb：含 session UUID
    """
    sapisid = cookies.get("SAPISID", cookies.get("__Secure-1PAPISID", ""))
    hash_val = _sapisidhash(sapisid) if sapisid else "0000000000000000"
    return {
        **_BASE_HEADERS_STATIC,
        "x-goog-ext-525001261-jspb": (
            f'[1,null,null,null,"{hash_val}",null,null,0,[4],null,null,1]'
        ),
        "x-goog-ext-525005358-jspb": f'["{session_uuid}",1]',
    }


def _fetch_at_token(session: requests.Session) -> tuple[str, str, str]:
    """
    對 gemini.google.com 首頁發 GET，從 HTML 中擷取：
    - at token（CSRF）
    - f.sid
    - bl（build label）
    回傳 (at_token, f_sid, bl)。
    """
    headers = {
        "accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "accept-language": "zh-TW,zh;q=0.9",
        "user-agent": _BASE_HEADERS_STATIC["user-agent"],
        "sec-fetch-dest": "document",
        "sec-fetch-mode": "navigate",
        "sec-fetch-site": "none",
    }
    resp = session.get("https://gemini.google.com/", headers=headers, timeout=30)
    resp.raise_for_status()
    html = resp.text

    at_token = ""
    f_sid = ""
    bl = ""

    # at token：SNlM0e 或 cfb2h（兩種已知格式）
    for pattern in [r'"SNlM0e":"([^"]+)"', r'"cfb2h":"([^"]+)"']:
        m = re.search(pattern, html)
        if m:
            at_token = m.group(1)
            break

    # f.sid
    m = re.search(r'"FdrFJe":"(-?\d+)"', html)
    if m:
        f_sid = m.group(1)

    # bl（build label）
    m = re.search(r'"cfb2h":"[^"]*"|"SNlM0e":"[^"]*".*?"WPF4fb":"([^"]+)"', html)
    if not m:
        m = re.search(r'boq_assistant-bard-web-server_[\d.]+_p\d+', html)
    if m:
        bl = m.group(0) if "boq_" in m.group(0) else ""

    return at_token, f_sid, bl


# ─────────────────────────────────────────────────────────────────────────────
# 回應物件（模擬 gemini_webapi 的介面）
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class GeneratedImage:
    """單張生成圖片，透過 .data 取得 PNG bytes。"""
    data: bytes

    @property
    def image(self) -> Image.Image:
        img = Image.open(io.BytesIO(self.data))
        img.load()
        return img


@dataclass
class GeminiResponse:
    """generate_content 的回傳值，模擬 gemini_webapi.ModelResponse。"""
    text: str = ""
    images: list[GeneratedImage] = field(default_factory=list)


# ─────────────────────────────────────────────────────────────────────────────
# 核心客戶端
# ─────────────────────────────────────────────────────────────────────────────

class GeminiWebClient:
    """
    以手刻 HTTP 直連 gemini.google.com 的非同步客戶端。

    at token、f.sid 均於 init() 時自動從首頁 HTML 擷取，不需手動維護。
    SAPISIDHASH 由 cookies 中的 SAPISID 動態計算。

    Parameters
    ----------
    cookies_path : str
        cookies.json 的路徑（由瀏覽器匯出的 JSON 陣列格式）。
    at_token : str
        可選的 override；留空則 init() 時自動抓取。
    f_sid : str
        可選的 override；留空則 init() 時自動抓取。
    reqid : str | int
        StreamGenerate _reqid，每次請求自動遞增。
    """

    def __init__(
        self,
        cookies_path: str = "cookies.json",
        at_token: str = "",
        f_sid: str = "",
        reqid: str | int = "1000001",
    ) -> None:
        self._cookies_path = cookies_path
        self._at_token = at_token
        self._f_sid = str(f_sid)
        self._reqid = int(reqid)
        self._bl = "boq_assistant-bard-web-server_20260405.11_p3"
        self._session: Optional[requests.Session] = None
        self._cookies: dict = {}
        self._session_uuid: str = str(uuid.uuid4()).upper()

    # ── 初始化 ──────────────────────────────────────────────────────────────

    async def init(self, **_kwargs) -> None:
        """
        建立 HTTP session、載入 cookies，
        並自動從 gemini.google.com 首頁抓取 at token 與 f.sid。
        """
        self._cookies = self._load_cookies(self._cookies_path)
        self._session = requests.Session()
        self._session.cookies.update(self._cookies)

        if not self._at_token or not self._f_sid:
            print("[GeminiWebClient] 自動取得 at token 與 f.sid...")
            try:
                at, f_sid, bl = await asyncio.to_thread(_fetch_at_token, self._session)
                if at:
                    self._at_token = at
                    print(f"[GeminiWebClient] at token 已取得：{at[:20]}...")
                else:
                    raise ValueError("首頁 HTML 中找不到 at token（SNlM0e / cfb2h）")
                if f_sid:
                    self._f_sid = f_sid
                    print(f"[GeminiWebClient] f.sid 已取得：{f_sid}")
                if bl:
                    self._bl = bl
            except Exception as exc:
                raise RuntimeError(
                    f"[GeminiWebClient] 自動取得 at token 失敗：{exc}\n"
                    "請確認 cookies.json 的 session 仍然有效（重新登入後重新匯出 cookies）。"
                ) from exc

    # ── 主要 API ────────────────────────────────────────────────────────────

    async def generate_content(
        self,
        prompt: str,
        model: str = "",
        files: Optional[list[str]] = None,
        private_mode: bool = False,
    ) -> GeminiResponse:
        """
        發送 prompt（可附圖片檔案路徑）給 Gemini，回傳 GeminiResponse。

        Parameters
        ----------
        prompt : str
            文字提示詞。
        model : str
            保留參數（相容舊呼叫，實際不使用）。
        files : list[str] | None
            圖片檔案路徑列表；目前取第一張使用。
        private_mode : bool
            是否啟用私人模式。False 時對應欄位送 None，True 時送 1。
        """
        if self._session is None:
            await self.init()

        # requests 為同步 I/O；在 asyncio 內直接呼叫會卡住事件迴圈，導致 SSE 無法即時送出。
        return await asyncio.to_thread(
            self._generate_content_sync, prompt, files, private_mode
        )

    def _generate_content_sync(
        self,
        prompt: str,
        files: Optional[list[str]],
        private_mode: bool,
    ) -> GeminiResponse:
        if self._session is None:
            raise RuntimeError("GeminiWebClient 尚未初始化")

        session = self._session
        image_path = files[0] if files else None

        image_token = ""
        filename = ""
        if image_path:
            image_token = self._upload_image(image_path, session)
            filename = os.path.basename(image_path)

        form_data = self._build_form_data(prompt, image_token, filename, private_mode)

        stream_url = (
            "https://gemini.google.com/_/BardChatUi/data/assistant.lamda.BardFrontendService/"
            f"StreamGenerate?bl={self._bl}"
            f"&f.sid={self._f_sid}&hl=zh-TW&_reqid={self._reqid}&rt=c"
        )
        self._reqid += 100000

        base_headers = _build_dynamic_headers(self._cookies, self._session_uuid)
        stream_headers = {**base_headers, "content-type": "application/x-www-form-urlencoded;charset=UTF-8"}

        print("[GeminiWebClient] 等待 Gemini 回應中（最多 300 秒）...")
        resp = session.post(stream_url, headers=stream_headers, data=form_data, timeout=300)
        if not resp.ok:
            print(f"[GeminiWebClient] StreamGenerate 失敗 {resp.status_code}：{resp.text[:500]}")
        resp.raise_for_status()

        return self._parse_response(resp.text, session)

    # ── 內部：上傳圖片 ──────────────────────────────────────────────────────

    def _upload_image(self, image_path: str, session: requests.Session) -> str:
        filename = os.path.basename(image_path)
        file_size = os.path.getsize(image_path)

        ext = os.path.splitext(filename)[1].lower()
        mime_map = {".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".png": "image/png", ".webp": "image/webp"}
        mime_type = mime_map.get(ext, "image/jpeg")

        print(f"[GeminiWebClient] 上傳圖片：{filename}，{file_size} bytes，{mime_type}")

        init_headers = {
            **{k: v for k, v in _BASE_HEADERS_STATIC.items()},
            "content-type": "application/x-www-form-urlencoded;charset=UTF-8",
            "push-id": "feeds/mcudyrk2a4khkz",
            "sec-fetch-site": "same-site",
            "x-client-pctx": "CgcSBWjK7pYx",
            "x-goog-upload-command": "start",
            "x-goog-upload-header-content-length": str(file_size),
            "x-goog-upload-protocol": "resumable",
            "x-tenant-id": "bard-storage",
        }
        init_headers["sec-fetch-site"] = "same-site"

        init_resp = session.post(
            _UPLOAD_BASE,
            headers=init_headers,
            data=f"File name: {filename}".encode(),
            timeout=30,
        )
        if init_resp.status_code != 200:
            raise RuntimeError(
                f"[GeminiWebClient] 上傳 Phase 1 失敗，狀態碼：{init_resp.status_code}\n"
                f"  回應：{init_resp.text[:300]}"
            )

        upload_url = (
            init_resp.headers.get("x-goog-upload-url")
            or init_resp.headers.get("location", "")
        )
        if not upload_url:
            raise RuntimeError(
                f"[GeminiWebClient] 上傳 Phase 1 未取得 upload URL\n"
                f"  headers：{dict(init_resp.headers)}\n"
                f"  body：{init_resp.text[:300]}"
            )

        upload_headers = {
            "accept": "*/*",
            "accept-encoding": "gzip, deflate, br, zstd",
            "content-type": mime_type,
            "origin": "https://gemini.google.com",
            "referer": "https://gemini.google.com/",
            "sec-fetch-dest": "empty",
            "sec-fetch-mode": "cors",
            "sec-fetch-site": "same-site",
            "user-agent": _BASE_HEADERS_STATIC["user-agent"],
            "x-goog-upload-command": "upload, finalize",
            "x-goog-upload-offset": "0",
        }
        with open(image_path, "rb") as f:
            file_data = f.read()

        upload_resp = session.post(upload_url, headers=upload_headers, data=file_data, timeout=60)
        upload_resp.raise_for_status()

        token_path = upload_resp.text.strip()
        if not token_path.startswith("/contrib_service"):
            raise RuntimeError(f"[GeminiWebClient] 上傳回傳格式非預期：{token_path[:300]}")

        print(f"[GeminiWebClient] 上傳成功，token: {token_path[:80]}")
        return token_path

    # ── 內部：組裝 f.req ────────────────────────────────────────────────────

    def _build_form_data(
        self,
        prompt: str,
        image_token: str,
        filename: str,
        private_mode: bool = False,
    ) -> str:
        mime_type = "image/jpeg" if filename.lower().endswith((".jpg", ".jpeg")) else "image/png"
        private_mode_flag = 1 if private_mode else None

        if image_token and filename:
            image_part: list = [
                [[image_token, 1, None, mime_type],
                 filename,
                 None, None, None, None, None, None, [0]]
            ]
        else:
            image_part = []

        inner_list = [
            [prompt, 0, None, image_part or None, None, None, 0],
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
            None, None, None, private_mode_flag, None, None, None, None, None, None, None,
            0,
            None, None, None, None, None,
            self._session_uuid,
            None, [],
            None, None, None, None, None, None,
            2,
            None, None, None, None, None, None, None, None, None, None,
            5,
        ]

        inner_str = json.dumps(inner_list, ensure_ascii=False, separators=(",", ":"))
        outer = [None, inner_str]
        f_req_val = json.dumps(outer, ensure_ascii=False, separators=(",", ":"))

        params: dict = {"f.req": f_req_val, "at": self._at_token}
        return urlencode(params) + "&"

    # ── 內部：解析串流回應 ──────────────────────────────────────────────────

    def _parse_response(self, raw_text: str, session: requests.Session) -> GeminiResponse:
        body = raw_text
        if body.startswith(")]}'"):
            body = body[4:]

        result_text = ""
        image_urls: list[str] = []

        for raw in re.split(r"\n\d+\n", body):
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

                try:
                    text_part = inner[4][0][1][0]
                    if isinstance(text_part, str) and text_part:
                        result_text = text_part
                except (IndexError, TypeError):
                    pass

                gen_urls = re.findall(
                    r"https://lh3\.googleusercontent\.com/gg-dl/[^\s'\"\\,\]\[]+",
                    inner_str,
                )
                image_urls.extend(gen_urls)

        image_urls = list(dict.fromkeys(image_urls))

        generated_images: list[GeneratedImage] = []
        for url in image_urls:
            img_data = self._download_bytes(url, session)
            if img_data:
                generated_images.append(GeneratedImage(data=img_data))

        return GeminiResponse(text=result_text, images=generated_images)

    # ── 內部：下載圖片 bytes ────────────────────────────────────────────────

    @staticmethod
    def _download_bytes(url: str, session: requests.Session) -> Optional[bytes]:
        try:
            resp = session.get(url, timeout=60, stream=True)
            resp.raise_for_status()
            chunks = []
            for chunk in resp.iter_content(chunk_size=8192):
                chunks.append(chunk)
            return b"".join(chunks)
        except Exception as exc:
            print(f"[GeminiWebClient] 圖片下載失敗：{url[:80]} → {exc}")
            return None

    # ── 內部：載入 cookies ──────────────────────────────────────────────────

    @staticmethod
    def _load_cookies(path: str) -> dict:
        with open(path, "r", encoding="utf-8") as f:
            entries = json.load(f)
        return {entry["name"]: entry["value"] for entry in entries}
