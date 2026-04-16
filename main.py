import argparse
import asyncio
import os
import sys

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8")

from core.config import parse_config, sync_managed_env_from_dotenv
from core.app_logging import get_backend_logger, setup_app_logging
from api.routers.tools.ecommerce_image.pipeline import run_pipeline

logger = get_backend_logger("cli")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="AI 電商圖文生成助手")
    parser.add_argument("--stage3-only", action="store_true", help="僅執行階段三（讀取 final_output.json 產圖）")
    return parser.parse_args()


async def async_main() -> None:
    args = parse_args()
    project_root = os.path.dirname(os.path.abspath(__file__))
    setup_app_logging(project_root)
    sync_managed_env_from_dotenv(os.path.join(project_root, ".env"))
    config = parse_config(
        stage3_only_flag=args.stage3_only,
    )

    logger.info("歡迎使用 AI 電商圖文生成助手！")
    result = await run_pipeline(config=config)
    logger.info("✅ 任務完成")
    logger.info("JSON 輸出：%s", result["final_output_path"])
    logger.info("圖片輸出數量：%s", len(result["saved_files"]))


if __name__ == "__main__":
    asyncio.run(async_main())
