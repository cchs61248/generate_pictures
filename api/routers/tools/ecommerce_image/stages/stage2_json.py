"""
電商圖文助手 - 階段二：P1~P9 JSON 腳本產生

將階段一蒐集的商品資訊，依 json_schema prompt 規範，
透過 LLM 產出 9 張圖的 JSON 腳本；失敗時進行二次修復。
"""
import asyncio
import json
import os

from api.deps import project_root
from core.app_logging import get_backend_logger
from core.config import get_text_model
from core.progress import GROUP_STAGE2_META, ProgressBus
from core.providers.base import ContentItem, TextProvider
from core.token_logger import log_token_usage

from api.routers.tools.ecommerce_image.prompts.json_schema import prompt_template
from api.routers.tools.ecommerce_image.services.style_learning import get_style_prompt_by_id
from api.routers.tools.ecommerce_image.utils.json_utils import (
    coerce_plan_array,
    extract_json_candidate,
    repair_to_json_apikey,
    validate_output,
)

# 獨立一行、上下空行 → CommonMark 解析為 <hr>，前端樣式化成細橫線分隔
_TOPIC_SEP = "-------------"
logger = get_backend_logger("stages.stage2_json")


def _preview_text(text: str, limit: int = 280) -> str:
    compact = " ".join((text or "").split())
    if len(compact) <= limit:
        return compact
    return compact[:limit] + "...(truncated)"


def _format_final_data_markdown(final_data: list[dict]) -> str:
    """緊湊 Markdown：標題用【】粗體（與素材區塊 UI 參考一致），主題間用橫線分隔。"""
    blocks: list[str] = []
    for item in final_data:
        main = str(item.get("main", "")).strip()
        scene = str(item.get("scene", "")).strip().replace("\n", " ")
        copy_block = item.get("copy") or {}
        headline = str(copy_block.get("headline", "")).strip()
        subline = str(copy_block.get("subline", "")).strip()
        tags_raw = copy_block.get("tags") or []
        if isinstance(tags_raw, list):
            tags_str = "、".join(str(t).strip() for t in tags_raw if str(t).strip())
        else:
            tags_str = str(tags_raw).strip()
        specs = str(item.get("specs", "")).strip().replace("\n", " ")
        lines = [
            f"**【{main}】**  ",
            f"**場景**：{scene}  ",
            f"**標題**：{headline}  ",
            f"**副標**：{subline}  ",
            f"**標籤**：{tags_str}  ",
            f"**規格**：{specs}",
        ]
        blocks.append("\n".join(lines))
    sep = f"\n\n{_TOPIC_SEP}\n\n"
    return sep.join(blocks)


async def generate_json_plan(
    gathered_info: str,
    image,
    text_provider: TextProvider,
    output_json_path: str,
    progress: ProgressBus | None = None,
    selected_style_profile_id: str | None = None,
) -> list[dict]:
    logger.info("[stage2] enter generate_json_plan")
    logger.debug(
        "[stage2] args | output_json_path=%s selected_style_profile_id=%s gathered_info_chars=%d",
        output_json_path,
        selected_style_profile_id or "(default)",
        len(gathered_info or ""),
    )
    if progress:
        await progress.emit(
            {
                "type": "collapsible_init",
                "group_id": GROUP_STAGE2_META,
                "title": "階段二 · 圖片腳本產生",
            }
        )
        await progress.emit(
            {
                "type": "collapsible_line",
                "group_id": GROUP_STAGE2_META,
                "line": "[階段二] 正在結合 json_schema 規範，生成最終的 AI 繪圖提示詞與文案...",
            }
        )

    logger.info("[stage2] 結合 json_schema 規範產生 P1~P9 腳本")
    style_prompt = get_style_prompt_by_id(
        root=project_root(),
        selected_profile_id=selected_style_profile_id,
    )
    stage2_system_instruction = prompt_template + (style_prompt or "")
    logger.debug(
        "[stage2] prepared system instruction | style_prompt_enabled=%s chars=%d",
        bool(style_prompt),
        len(stage2_system_instruction),
    )

    format_prompt = f"""
請根據以下我為你收集好的商品資訊，以及我上傳的商品圖片，
嚴格按照系統提示詞中的固定格式（P1~P9），
生成完整的電商商品視覺設計與行銷文案。

【商品資訊】
{gathered_info}
"""

    logger.info(
        "[stage2] built prompt payload | prompt_chars=%d",
        len(format_prompt),
    )
    logger.debug("[stage2] prompt preview: %s", _preview_text(format_prompt, 600))
    logger.debug(
        "[stage2] system instruction preview: %s",
        _preview_text(stage2_system_instruction, 600),
    )

    logger.info("[stage2] calling text provider generate_text")
    user_content = [
        ContentItem(type="text", text=format_prompt),
        ContentItem(type="image_pil", pil_image=image),
    ]
    result = await text_provider.generate_text(
        model=get_text_model(),
        system=stage2_system_instruction,
        user_content=user_content,
        temperature=0.0,
        json_mode=True,
    )
    logger.debug(
        "[stage2] usage | input_tokens=%s output_tokens=%s",
        result.input_tokens,
        result.output_tokens,
    )
    try:
        log_token_usage(
            model=get_text_model(),
            source="stage2_json",
            input_tokens=result.input_tokens,
            output_tokens=result.output_tokens,
        )
    except Exception:
        pass
    raw_output = result.text or ""
    logger.info("[stage2] received model response | raw_chars=%d", len(raw_output))
    logger.debug("[stage2] raw output preview: %s", _preview_text(raw_output, 800))

    candidate = extract_json_candidate(raw_output)
    final_data = None
    try:
        parsed = coerce_plan_array(json.loads(candidate))
        ok, reason = validate_output(parsed)
        if ok:
            final_data = parsed
        else:
            msg = f"⚠️ [格式檢查] 首次輸出未通過：{reason}"
            logger.warning(msg)
            if progress:
                await progress.emit(
                    {
                        "type": "collapsible_line",
                        "group_id": GROUP_STAGE2_META,
                        "line": msg,
                    }
                )
    except Exception as exc:
        msg = f"⚠️ [格式檢查] 首次輸出非合法 JSON：{exc}"
        logger.warning(msg)
        if progress:
            await progress.emit(
                {
                    "type": "collapsible_line",
                    "group_id": GROUP_STAGE2_META,
                    "line": msg,
                }
            )

    if final_data is None:
        line = "[修復流程] 正在嘗試二次修復輸出格式..."
        logger.info(line)
        if progress:
            await progress.emit(
                {
                    "type": "collapsible_line",
                    "group_id": GROUP_STAGE2_META,
                    "line": line,
                }
            )
        logger.info("[stage2] repair path | api_key")
        repaired = coerce_plan_array(await repair_to_json_apikey(text_provider, raw_output))
        ok, reason = validate_output(repaired)
        if not ok:
            raise ValueError(f"修復後仍不符合 JSON 規範：{reason}")
        final_data = repaired

    logger.info("[stage2] JSON validated | topics=%d", len(final_data))
    logger.debug(
        "[stage2] final JSON preview: %s",
        _preview_text(json.dumps(final_data, ensure_ascii=False), 1200),
    )

    out_dir = os.path.dirname(output_json_path)
    if out_dir:
        os.makedirs(out_dir, exist_ok=True)
    with open(output_json_path, "w", encoding="utf-8") as file:
        json.dump(final_data, file, ensure_ascii=False, indent=2)
    logger.info("[stage2] JSON saved: %s", output_json_path)
    if progress:
        await progress.emit(
            {
                "type": "collapsible_line",
                "group_id": GROUP_STAGE2_META,
                "line": f"💾 [JSON 已儲存] {output_json_path}",
            }
        )
        markdown_plan = _format_final_data_markdown(final_data)
        await progress.emit(
            {
                "type": "text_block",
                "format": "markdown",
                "content": f"🤖 **[圖片腳本]**\n\n{markdown_plan}",
            }
        )
    logger.info("[stage2] exit generate_json_plan")
    return final_data
