import json
import os

import requests
from bs4 import BeautifulSoup
from ddgs import DDGS

from core.progress import GROUP_STAGE1_TOOLS, get_progress_bus

TAVILY_SEARCH_URL = "https://api.tavily.com/search"

# LLM automatic_function_calling 單次對話中，search_web 實際查詢次數上限（不含觸發上限時的回傳訊息）。
MAX_LLM_SEARCH_CALLS = 4


def make_bounded_search_web(max_calls: int | None = None):
    """包一層計數器，超過上限時不再打外部搜尋 API，避免 LLM 反覆搜尋。"""
    limit = max_calls if max_calls is not None else MAX_LLM_SEARCH_CALLS
    used = 0

    def bounded_search_web(query: str) -> str:
        nonlocal used
        if used >= limit:
            return (
                f"【系統】網路搜尋已達本次上限（{limit} 次），請勿再呼叫搜尋工具，"
                "請改用圖片、使用者文字、已讀取的網頁內容與既有結果繼續整理；"
                "資訊不足處請標注「待確認」。"
            )
        used += 1
        return search_web(query)

    return bounded_search_web


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
