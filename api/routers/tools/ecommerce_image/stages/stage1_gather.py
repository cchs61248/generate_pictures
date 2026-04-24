"""
電商圖文助手 - 階段一：商品資訊蒐集

依使用者輸入（文字描述或網址），結合上傳圖片，
透過 LLM + 搜尋/抓頁工具，產出供 P1~P9 使用的結構化商品素材。
"""
import asyncio
import re

from api.deps import project_root
from api.routers.tools.ecommerce_image.services.style_learning import get_style_prompt_by_id
from core.app_logging import get_backend_logger
from core.config import get_text_model
from core.progress import GROUP_STAGE1_TOOLS, ProgressBus, progress_cv
from core.providers.base import ContentItem, TextProvider
from core.token_logger import log_token_usage
from services.web_search import fetch_webpage, get_max_llm_search_calls, make_bounded_search_web

logger = get_backend_logger("stages.stage1_gather")


def _preview_text(text: str, limit: int = 280) -> str:
    compact = " ".join((text or "").split())
    if len(compact) <= limit:
        return compact
    return compact[:limit] + "...(truncated)"


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



async def gather_product_info(
    user_input: str,
    image,
    text_provider: TextProvider,
    doc_texts: list[str] | None = None,
    doc_filenames: list[str] | None = None,
    progress: ProgressBus | None = None,
    selected_style_profile_id: str | None = None,
) -> str:
    logger.info("[stage1] enter gather_product_info")
    logger.debug(
        "[stage1] args | doc_count=%d selected_style_profile_id=%s",
        len(doc_texts or []),
        selected_style_profile_id or "(default)",
    )
    ctx_token = None
    model_name = get_text_model()
    if progress:
        await progress.emit(
            {
                "type": "collapsible_init",
                "group_id": GROUP_STAGE1_TOOLS,
                "title": "階段一 · 工具與系統提示",
                "model": model_name,
            }
        )
        await progress.emit(
            {
                "type": "collapsible_line",
                "group_id": GROUP_STAGE1_TOOLS,
                "line": "[階段一] 正在分析圖片與聯網收集商品資訊，請稍候...",
                "model": model_name,
            }
        )
        ctx_token = progress_cv.set(progress)

    logger.info("[stage1] 分析圖片與聯網蒐集商品資訊")
    dedup_urls = _extract_urls(user_input)
    logger.info("[stage1] extracted urls | count=%d", len(dedup_urls))
    if dedup_urls:
        logger.debug("[stage1] url list: %s", " | ".join(dedup_urls))

    if dedup_urls:
        url_mandatory_block = (
            "查看使用者訊息會和附件內容，若附件內容包含商品資訊，則優先使用附件內容作為商品資訊的補充參考。\n"
            "【工具使用規則】:如使用提供資訊不足，則使用工具取得實際內容。\n"
            "使用者輸入含下列網址，必須先呼叫 fetch_webpage 工具取得實際內容，禁止推測頁面內容，\n"
            "禁止在未瀏覽前推測該網址上的規格或文案。\n"
            f"網址清單：{'、'.join(dedup_urls)}"
        )
    else:
        url_mandatory_block = (
            "查看使用者訊息會和附件內容，若附件內容包含商品資訊，則優先使用附件內容作為商品資訊的補充參考。\n"
            "【工具使用規則】:如使用提供資訊不足，則使用工具取得實際內容。\n"
            "【網址規則】若使用者訊息中出現 http(s) 網址，必須先呼叫 fetch_webpage 工具取得實際內容，禁止推測頁面內容。\n"
            "禁止在未瀏覽前推測該網址上的規格或文案。\n"
        )
    style_prompt = get_style_prompt_by_id(
        root=project_root(),
        selected_profile_id=selected_style_profile_id,
    )
    stage1_system_instruction = url_mandatory_block + (style_prompt or "")
    logger.debug(
        "[stage1] prepared system instruction | style_prompt_enabled=%s chars=%d",
        bool(style_prompt),
        len(stage1_system_instruction),
    )

    doc_block = ""
    if doc_texts:
        names = doc_filenames or []
        for i, _ in enumerate(doc_texts, 1):
            filename = names[i - 1] if i - 1 < len(names) else f"文件_{i}"
            line = f"👉 [系統提示] 已讀取附件文件: {filename}"
            if progress:
                await progress.emit(
                    {
                        "type": "collapsible_line",
                        "group_id": GROUP_STAGE1_TOOLS,
                        "line": line,
                        "model": model_name,
                    }
                )
        doc_sections = []
        for i, text in enumerate(doc_texts, 1):
            doc_sections.append(f"--- 文件 {i} ---\n{text}")
        doc_block = (
            "\n【使用者附件文件內容】\n"
            "以下為使用者提供的文件，請將其內容納入分析，優先作為商品資訊的補充參考：\n\n"
            + "\n\n".join(doc_sections)
            + "\n"
        )
    logger.info(
        "[stage1] prepared doc context | doc_count=%d doc_block_chars=%d",
        len(doc_texts or []),
        len(doc_block),
    )

    info_prompt = f"""
請仔細分析我上傳的商品圖片，並結合以下用戶提供的文字或網址資訊：
「{user_input}」
{doc_block}
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

    logger.info(
        "[stage1] built prompt payload | prompt_chars=%d user_input_preview=%s",
        len(info_prompt),
        _preview_text(user_input, 120),
    )
    logger.debug("[stage1] prompt preview: %s", _preview_text(info_prompt, 600))

    try:
        logger.info("[stage1] calling text provider chat_with_tools flow")
        bounded_search = make_bounded_search_web()
        user_content = [
            ContentItem(type="text", text=info_prompt),
            ContentItem(type="image_pil", pil_image=image),
        ]
        result = await text_provider.chat_with_tools(
            model=model_name,
            system=stage1_system_instruction,
            user_content=user_content,
            tool_fns=[bounded_search, fetch_webpage],
            max_tool_calls=get_max_llm_search_calls() + 2,
        )
        logger.debug(
            "[stage1] usage | input_tokens=%s output_tokens=%s",
            result.input_tokens,
            result.output_tokens,
        )
        try:
            log_token_usage(
                model=model_name,
                source="stage1_gather",
                input_tokens=result.input_tokens,
                output_tokens=result.output_tokens,
            )
        except Exception:
            pass
        gathered_info = result.text

        logger.info("[stage1] completed gather_product_info | output_chars=%d", len(gathered_info))
        logger.debug("[stage1] gathered info preview: %s", _preview_text(gathered_info, 800))
        if progress:
            md = (
                "✅ **[階段一完成]** 收集到的商品資訊如下：\n\n---\n\n"
                f"{gathered_info}"
            )
            await progress.emit(
                {
                    "type": "text_block",
                    "format": "markdown",
                    "content": md,
                    "model": model_name,
                }
            )
        return gathered_info
    finally:
        if ctx_token is not None:
            progress_cv.reset(ctx_token)
        logger.info("[stage1] exit gather_product_info")
