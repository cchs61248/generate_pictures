import json
import os

import requests
from bs4 import BeautifulSoup
from ddgs import DDGS

from core.progress import GROUP_STAGE1_TOOLS, get_progress_bus

TAVILY_SEARCH_URL = "https://api.tavily.com/search"


def _search_tavily(query: str, api_key: str) -> str:
    response = requests.post(
        TAVILY_SEARCH_URL,
        json={"api_key": api_key, "query": query, "max_results": 3},
        timeout=30,
    )
    response.raise_for_status()
    data = response.json()
    return json.dumps(data.get("results", []), ensure_ascii=False)


def search_web(query: str) -> str:
    line = f"👉 [系統提示] 觸發搜尋工具，關鍵字: {query}"
    print(f"\n{line}")
    bus = get_progress_bus()
    if bus:
        bus.emit_sync(
            {"type": "collapsible_line", "group_id": GROUP_STAGE1_TOOLS, "line": line}
        )
    api_key = (os.environ.get("TAVILY_API_KEY") or "").strip()
    if api_key:
        try:
            return _search_tavily(query, api_key)
        except Exception:
            pass
    try:
        results = DDGS().text(query, max_results=3)
        return str(results)
    except Exception as exc:
        return f"搜尋失敗: {exc}"


def fetch_webpage(url: str) -> str:
    line = f"👉 [系統提示] 觸發網頁讀取工具，正在訪問網址: {url}"
    print(f"\n{line}")
    bus = get_progress_bus()
    if bus:
        bus.emit_sync(
            {"type": "collapsible_line", "group_id": GROUP_STAGE1_TOOLS, "line": line}
        )
    try:
        headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}
        response = requests.get(url, headers=headers, timeout=10)
        response.raise_for_status()
        soup = BeautifulSoup(response.text, "html.parser")
        text = soup.get_text(separator="\n", strip=True)
        return text[:5000]
    except Exception as exc:
        return f"無法讀取網頁: {exc}"
