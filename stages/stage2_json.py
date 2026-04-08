import json

from google.genai import types

from core.config import TEXT_MODEL
from prompts.json_schema import prompt_template
from utils.json_utils import extract_json_candidate, repair_to_json_apikey, repair_to_json_webapi, validate_output


async def generate_json_plan(
    gathered_info: str,
    image,
    image_path: str,
    genai_client,
    gemini_client,
    use_webapi: bool,
    output_json_path: str,
) -> list[dict]:
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
        response = genai_client.models.generate_content(
            model=TEXT_MODEL,
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
            print(f"\n⚠️ [格式檢查] 首次輸出未通過：{reason}")
    except Exception as exc:
        print(f"\n⚠️ [格式檢查] 首次輸出非合法 JSON：{exc}")

    if final_data is None:
        print("[修復流程] 正在嘗試二次修復輸出格式...")
        if use_webapi:
            repaired = await repair_to_json_webapi(gemini_client, raw_output)
        else:
            repaired = repair_to_json_apikey(genai_client, raw_output)
        ok, reason = validate_output(repaired)
        if not ok:
            raise ValueError(f"修復後仍不符合 JSON 規範：{reason}")
        final_data = repaired

    print("\n🤖 [最終輸出] JSON:")
    print("=" * 60)
    print(json.dumps(final_data, ensure_ascii=False, indent=2))
    print("=" * 60)

    with open(output_json_path, "w", encoding="utf-8") as file:
        json.dump(final_data, file, ensure_ascii=False, indent=2)
    print(f"💾 [JSON 已儲存] {output_json_path}")
    return final_data
