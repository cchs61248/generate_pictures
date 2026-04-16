import contextlib
import ctypes
import os
import socket
import sys
import threading
import time
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import uvicorn
from playwright.sync_api import Error as PlaywrightError
from playwright.sync_api import sync_playwright

from api.server import app


if getattr(sys, "frozen", False):
    BUNDLE_ROOT = Path(getattr(sys, "_MEIPASS"))
else:
    BUNDLE_ROOT = Path(__file__).resolve().parent

FRONTEND_DIST = BUNDLE_ROOT / "frontend" / "dist"
RUNTIME_ROOT = Path.cwd().resolve()
API_HOST = "127.0.0.1"
API_PORT = 8000
FRONTEND_HOST = "127.0.0.1"
FRONTEND_PORT = 5173


def get_screen_size() -> tuple[int, int]:
    user32 = ctypes.windll.user32
    return user32.GetSystemMetrics(0), user32.GetSystemMetrics(1)


def wait_for_port(host: str, port: int, timeout_seconds: float = 20.0) -> None:
    deadline = time.time() + timeout_seconds
    while time.time() < deadline:
        with contextlib.closing(socket.socket(socket.AF_INET, socket.SOCK_STREAM)) as sock:
            sock.settimeout(1.0)
            if sock.connect_ex((host, port)) == 0:
                return
        time.sleep(0.2)
    raise RuntimeError(f"等待服務啟動逾時: {host}:{port}")


def start_backend_server() -> tuple[uvicorn.Server, threading.Thread]:
    config = uvicorn.Config(app, host=API_HOST, port=API_PORT, log_level="info")
    server = uvicorn.Server(config)
    thread = threading.Thread(target=server.run, daemon=True, name="backend-uvicorn")
    thread.start()
    wait_for_port(API_HOST, API_PORT)
    return server, thread


def start_frontend_server() -> tuple[ThreadingHTTPServer, threading.Thread]:
    if not FRONTEND_DIST.exists():
        raise FileNotFoundError(
            "找不到 frontend/dist，請先在 frontend 目錄執行 npm run build。"
        )
    handler = partial(SimpleHTTPRequestHandler, directory=str(FRONTEND_DIST))
    server = ThreadingHTTPServer((FRONTEND_HOST, FRONTEND_PORT), handler)
    thread = threading.Thread(
        target=server.serve_forever,
        daemon=True,
        name="frontend-static-server",
    )
    thread.start()
    wait_for_port(FRONTEND_HOST, FRONTEND_PORT)
    return server, thread


def open_browser_with_playwright(url: str) -> None:
    # 強制使用套件內的瀏覽器，不使用使用者本機 Edge/Chrome。
    os.environ["PLAYWRIGHT_BROWSERS_PATH"] = "0"
    screen_width, screen_height = get_screen_size()
    with sync_playwright() as playwright:
        try:
            browser = playwright.chromium.launch(
                headless=False,
                args=[
                    f"--window-size={screen_width},{screen_height}",
                    "--window-position=0,0",
                ],
            )
            print("[launcher] 使用內建 Playwright Chromium 開啟瀏覽器。")
        except PlaywrightError as exc:
            raise RuntimeError(
                "找不到內建 Playwright Chromium。請先執行 "
                "`$env:PLAYWRIGHT_BROWSERS_PATH='0'; .\\.venv\\Scripts\\python.exe -m playwright install chromium` "
                "並重新打包 EXE。"
            ) from exc

        # 關閉 Playwright 預設 1280x720 視窗模擬，讓頁面跟隨真實瀏覽器尺寸。
        context = browser.new_context(no_viewport=True)
        page = context.new_page()
        page.goto(url)
        print(f"[launcher] 已開啟瀏覽器：{url}")
        print("[launcher] 關閉瀏覽器或按 Ctrl+C 可結束程式。")
        try:
            while browser.is_connected():
                time.sleep(1)
        finally:
            if browser.is_connected():
                browser.close()


def main() -> None:
    os.environ["APP_RUNTIME_ROOT"] = str(RUNTIME_ROOT)
    os.chdir(RUNTIME_ROOT)
    print(f"[launcher] 執行資料目錄：{RUNTIME_ROOT}")
    print("[launcher] 啟動後端服務...")
    backend_server, _ = start_backend_server()
    print("[launcher] 啟動前端靜態服務...")
    frontend_server, _ = start_frontend_server()
    try:
        open_browser_with_playwright(f"http://{FRONTEND_HOST}:{FRONTEND_PORT}")
    except KeyboardInterrupt:
        print("\n[launcher] 接收到中斷訊號，準備關閉服務...")
    except Exception as exc:
        print(f"[launcher] 自動開啟瀏覽器失敗：{exc}")
        print(f"[launcher] 你仍可手動開啟：http://{FRONTEND_HOST}:{FRONTEND_PORT}")
        print("[launcher] 按 Ctrl+C 結束程式。")
        try:
            while True:
                time.sleep(1)
        except KeyboardInterrupt:
            print("\n[launcher] 接收到中斷訊號，準備關閉服務...")
    finally:
        frontend_server.shutdown()
        frontend_server.server_close()
        backend_server.should_exit = True
        print("[launcher] 已關閉。")


if __name__ == "__main__":
    main()
