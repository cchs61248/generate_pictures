"""
電商圖文助手 - 圖片 Prompt 組合服務

負責將 P1~P9 的 JSON 項目與風格模板組合成送給圖像模型的完整 prompt。
"""


def build_safe_name(main_name: str) -> str:
    return main_name.replace(" ", "_").replace("/", "_").replace("：", "")


def compose_image_prompt(item: dict) -> str:
    copy_block = item["copy"]
    tags = copy_block["tags"]
    tags_quoted = " ".join(f"《{t}》" for t in tags)
    return f"""### {item["main"]}
- scene：{item["scene"]}
- specs：{item["specs"]}

#### 必須顯示的文字（請逐字繪製於圖中）
- 主標：《{copy_block["headline"]}》
- 副標：《{copy_block["subline"]}》
- 標籤（可分行或橫排，每則須完整）：{tags_quoted}
"""
