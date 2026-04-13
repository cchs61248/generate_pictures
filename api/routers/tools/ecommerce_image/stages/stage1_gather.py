"""
電商圖文助手 - 階段一：商品資訊蒐集

依使用者輸入（文字描述或網址），結合上傳圖片，
透過 LLM + 搜尋/抓頁工具，產出供 P1~P9 使用的結構化商品素材。
"""
import asyncio
import re

from google.genai import types

from core.config import get_text_model
from core.progress import GROUP_STAGE1_TOOLS, ProgressBus, progress_cv
from core.token_logger import log_token_usage
from services.web_search import fetch_webpage, get_max_llm_search_calls, make_bounded_search_web


def _extract_urls(user_input: str) -> list[str]:
    url_re = re.compile(r"https?://[^\s<>'\"\]\)]+", re.IGNORECASE)
    dedup_urls: list[str] = []
    seen_urls: set[str] = set()
    for match in url_re.finditer(user_input or ""):
        url = match.group(0).rstrip(".,);]\"'")
        if url and url not in seen_urls:
            seen_urls.add(url)
            dedup_urls.append(url)
    return dedup_urls


def _response_text_safe(response) -> str:
    """避免直接讀 response.text 觸發 SDK non-text parts 警告。"""
    candidates = getattr(response, "candidates", None)
    if candidates:
        text_parts: list[str] = []
        for cand in candidates:
            content = getattr(cand, "content", None)
            parts = getattr(content, "parts", None) if content else None
            if not parts:
                continue
            for part in parts:
                text = getattr(part, "text", None)
                if isinstance(text, str) and text:
                    text_parts.append(text)
        if text_parts:
            return "\n".join(text_parts).strip()

    text = getattr(response, "text", "")
    return text or ""


async def gather_product_info(
    user_input: str,
    image,
    image_path: str,
    genai_client,
    gemini_client,
    use_webapi: bool,
    progress: ProgressBus | None = None,
) -> str:
    ctx_token = None
    if progress:
        await progress.emit(
            {
                "type": "collapsible_init",
                "group_id": GROUP_STAGE1_TOOLS,
                "title": "階段一 · 工具與系統提示",
            }
        )
        await progress.emit(
            {
                "type": "collapsible_line",
                "group_id": GROUP_STAGE1_TOOLS,
                "line": "[階段一] 正在分析圖片與聯網收集商品資訊，請稍候...",
            }
        )
        ctx_token = progress_cv.set(progress)

    print("\n[階段一] 正在分析圖片與聯網收集商品資訊，請稍候...")
    dedup_urls = _extract_urls(user_input)

    if dedup_urls:
        url_mandatory_block = (
            "【強制工具】使用者輸入含下列網址，必須先呼叫 fetch_webpage 工具取得實際內容，禁止推測頁面內容，\n"
            "禁止在未瀏覽前推測該網址上的規格或文案。\n"
            f"網址清單：{'、'.join(dedup_urls)}"
        )
    else:
        url_mandatory_block = "【網址規則】若使用者訊息中出現 http(s) 網址，必須先呼叫 fetch_webpage 工具取得實際內容，禁止推測頁面內容。"

    info_prompt = f"""
請仔細分析我上傳的商品圖片，並結合以下用戶提供的文字或網址資訊：
「{user_input}」
{url_mandatory_block}
請主動搜尋相關資料，依照以下結構逐項整理，這些資訊將直接用於後續生成 9 張電商商品圖的 AI 繪圖提示詞（P1～P9）：

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

    try:
        if use_webapi:
            response = await gemini_client.generate_content(
                info_prompt,
                model="gemini-3-flash-thinking",
                files=[image_path],
            )
            gathered_info = _response_text_safe(response)
        else:
            bounded_search = make_bounded_search_web()
            info_chat = genai_client.chats.create(
                model=get_text_model(),
                config=types.GenerateContentConfig(
                    tools=[bounded_search, fetch_webpage],
                    automatic_function_calling=types.AutomaticFunctionCallingConfig(
                        disable=False,
                        # 搜尋最多 get_max_llm_search_calls() 次（見 make_bounded_search_web）；其餘額度給 fetch_webpage
                        maximum_remote_calls=get_max_llm_search_calls() + 25,
                    ),
                ),
            )
            # send_message 為同步長時間呼叫，須移出事件迴圈以免 SSE 進度無法即時推送
            response = await asyncio.to_thread(
                info_chat.send_message,
                [info_prompt, image],
            )
            usage = getattr(response, "usage_metadata", None)
            if usage is not None:
                try:
                    log_token_usage(
                        model=get_text_model(),
                        source="stage1_gather",
                        input_tokens=getattr(usage, "prompt_token_count", 0) or 0,
                        output_tokens=getattr(usage, "candidates_token_count", 0) or 0,
                    )
                except Exception:
                    pass
            gathered_info = _response_text_safe(response)

        print("\n✅ [階段一完成] 收集到的商品資訊如下：")
        print("-" * 40)
        print(gathered_info)
        print("-" * 40)
        if progress:
            md = (
                "✅ **[階段一完成]** 收集到的商品資訊如下：\n\n---\n\n"
                f"{gathered_info}"
            )
            await progress.emit(
                {"type": "text_block", "format": "markdown", "content": md}
            )
        return gathered_info
    finally:
        if ctx_token is not None:
            progress_cv.reset(ctx_token)
