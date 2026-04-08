import argparse
import asyncio
import os

from core.config import load_env_file, parse_config
from core.pipeline import run_pipeline


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="AI 電商圖文生成助手")
    parser.add_argument("--stage3-only", action="store_true", help="僅執行階段三（讀取 final_output.json 產圖）")
    return parser.parse_args()


async def async_main() -> None:
    args = parse_args()
    project_root = os.path.dirname(os.path.abspath(__file__))
    load_env_file(os.path.join(project_root, ".env"))
    config = parse_config(
        stage3_only_flag=args.stage3_only,
    )

    print("歡迎使用 AI 電商圖文生成助手！")
    result = await run_pipeline(config=config)
    print("\n✅ 任務完成")
    print(f"JSON 輸出：{result['final_output_path']}")
    print(f"圖片輸出數量：{len(result['saved_files'])}")


if __name__ == "__main__":
    asyncio.run(async_main())
