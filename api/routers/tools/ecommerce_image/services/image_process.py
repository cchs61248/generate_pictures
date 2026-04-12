"""
電商圖文助手 - 圖片 Prompt 組合服務

負責將 P1~P9 的 JSON 項目與風格模板組合成送給圖像模型的完整 prompt。
"""


def build_safe_name(main_name: str) -> str:
    return main_name.replace(" ", "_").replace("/", "_").replace("：", "")


def compose_image_prompt(style_prompt: str, item: dict) -> str:
    copy_block = item["copy"]
    tags_str = "、".join(copy_block["tags"])
    return f"""{style_prompt}

### {item["main"]}
- scene：{item["scene"]}
- headline：{copy_block["headline"]}
- subline：{copy_block["subline"]}
- tags：{tags_str}
- specs：{item["specs"]}
"""
