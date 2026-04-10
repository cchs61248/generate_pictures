import asyncio
import json
import os

from google.genai import types

from core.config import get_text_model
from core.progress import GROUP_STAGE2_META, ProgressBus
from prompts.json_schema import prompt_template
from utils.json_utils import extract_json_candidate, repair_to_json_apikey, repair_to_json_webapi, validate_output

# 獨立一行、上下空行 → CommonMark 解析為 <hr>，前端樣式化成細橫線分隔
_TOPIC_SEP = "-------------"


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
    image_path: str,
    genai_client,
    gemini_client,
    use_webapi: bool,
    output_json_path: str,
    progress: ProgressBus | None = None,
) -> list[dict]:
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
                "line": "[階段二] 正在結合 prompts/json_schema.py 規範，生成最終的 AI 繪圖提示詞與文案...",
            }
        )

    print("\n[階段二] 正在結合 prompts/json_schema.py 規範，生成最終的 AI 繪圖提示詞與文案...")

    format_prompt = f"""
請根據以下我為你收集好的商品資訊，以及我上傳的商品圖片，
嚴格按照系統提示詞中的固定格式（P1~P9），
生成完整的電商商品視覺設計與行銷文案。

{prompt_template}

【商品資訊】
{gathered_info}
"""

    if use_webapi:
        response = await gemini_client.generate_content(
            format_prompt,
            model="gemini-3-flash-thinking",
            files=[image_path],
        )
        raw_output = response.text or ""
    else:
        response = await asyncio.to_thread(
            genai_client.models.generate_content,
            model=get_text_model(),
            contents=[format_prompt, image],
            config=types.GenerateContentConfig(
                system_instruction=prompt_template,
                temperature=0.0,
                response_mime_type="application/json",
            ),
        )
        raw_output = response.text or ""

    candidate = extract_json_candidate(raw_output)
    final_data = None
    try:
        parsed = json.loads(candidate)
        ok, reason = validate_output(parsed)
        if ok:
            final_data = parsed
        else:
            msg = f"⚠️ [格式檢查] 首次輸出未通過：{reason}"
            print(f"\n{msg}")
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
        print(f"\n{msg}")
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
        print(line)
        if progress:
            await progress.emit(
                {
                    "type": "collapsible_line",
                    "group_id": GROUP_STAGE2_META,
                    "line": line,
                }
            )
        if use_webapi:
            repaired = await repair_to_json_webapi(gemini_client, raw_output)
        else:
            repaired = await asyncio.to_thread(
                repair_to_json_apikey, genai_client, raw_output
            )
        ok, reason = validate_output(repaired)
        if not ok:
            raise ValueError(f"修復後仍不符合 JSON 規範：{reason}")
        final_data = repaired

    print("\n🤖 [最終輸出] JSON:")
    print("=" * 60)
    print(json.dumps(final_data, ensure_ascii=False, indent=2))
    print("=" * 60)

    out_dir = os.path.dirname(output_json_path)
    if out_dir:
        os.makedirs(out_dir, exist_ok=True)
    with open(output_json_path, "w", encoding="utf-8") as file:
        json.dump(final_data, file, ensure_ascii=False, indent=2)
    print(f"💾 [JSON 已儲存] {output_json_path}")
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
    return final_data
