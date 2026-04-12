"""
電商圖文助手 - JSON 工具函式

負責：
  - P1~P9 JSON 候選文字抽取
  - 嚴格格式驗證（9 筆、固定欄位、固定 main 值）
  - LLM 二次修復（API 模式與 Web 模式）
"""
import json
import re

from google.genai import types

from core.config import get_text_model
from api.routers.tools.ecommerce_image.prompts.json_schema import prompt_template


# P1~P9 固定的 main 欄位值（從 core/config.py 移入，屬電商工具專屬）
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


def extract_json_candidate(text: str) -> str:
    cleaned = text.strip()
    if cleaned.startswith("[") and cleaned.endswith("]"):
        return cleaned
    match = re.search(r"\[\s*\{[\s\S]*\}\s*\]", cleaned)
    if match:
        return match.group(0)
    return cleaned


def validate_output(data: object) -> tuple[bool, str]:
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


def repair_to_json_apikey(genai_client, raw_text: str) -> list[dict]:
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
    repaired = genai_client.models.generate_content(
        model=get_text_model(),
        contents=repair_prompt,
        config=types.GenerateContentConfig(
            system_instruction=prompt_template,
            temperature=0.0,
        ),
    )
    return json.loads(extract_json_candidate(repaired.text or ""))


async def repair_to_json_webapi(gemini_client, raw_text: str) -> list[dict]:
    repair_prompt = f"""
{prompt_template}

請把以下內容修復成「合法 JSON」，並且只輸出 JSON 陣列本體，不要有任何額外文字。
必須符合：
1. 根節點是長度 9 的陣列（P1~P9）。
2. 每筆固定 key：sort, main, scene, copy, specs。
3. copy 固定 key：headline, subline, tags。
4. main 與 sort 必須是既定順序與固定值。

以下是待修復原文：
{raw_text}
"""
    repaired = await gemini_client.generate_content(repair_prompt, model="gemini-3-flash-thinking")
    return json.loads(extract_json_candidate(repaired.text or ""))
