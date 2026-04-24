"""
Provider 抽象層 — 統一文字與圖像模型的呼叫介面，支援 Gemini 與 OpenAI（含 compatible API）。
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any


@dataclass
class ContentItem:
    """跨 provider 統一的輸入內容單元。"""
    type: str  # "text" | "image_pil" | "image_bytes"
    text: str | None = None
    pil_image: Any | None = None      # PIL.Image
    image_bytes: bytes | None = None
    mime_type: str = "image/jpeg"


@dataclass
class TextResult:
    text: str
    input_tokens: int = 0
    output_tokens: int = 0


@dataclass
class ImageResult:
    image_bytes: bytes
    input_tokens: int = 0
    output_tokens: int = 0


@dataclass
class EditResult:
    text: str = ""
    image_bytes: bytes | None = None
    input_tokens: int = 0
    output_tokens: int = 0
    # 多輪狀態；OpenAI 存 {"image_call_id": "..."}，Gemini 不使用
    provider_state: dict | None = None


class TextProvider(ABC):
    @abstractmethod
    async def chat_with_tools(
        self,
        model: str,
        system: str,
        user_content: list[ContentItem],
        tool_fns: list,
        max_tool_calls: int,
    ) -> TextResult:
        """帶工具呼叫的對話（用於階段一）。"""

    @abstractmethod
    async def generate_text(
        self,
        model: str,
        system: str,
        user_content: list[ContentItem],
        temperature: float = 0.0,
        json_mode: bool = False,
    ) -> TextResult:
        """純文字生成（用於階段二、JSON 修復）。"""


class ImageProvider(ABC):
    @abstractmethod
    async def generate_image(
        self,
        model: str,
        prompt: str,
        reference_image_pil: Any,  # PIL.Image
        style_instruction: str,
        image_size: str,
    ) -> ImageResult:
        """依商品參考圖生成電商圖（用於階段三）。"""

    @abstractmethod
    async def edit_image(
        self,
        model: str,
        image_bytes: bytes,
        prompt: str,
        style_instruction: str,
        image_size: str,
        previous_provider_state: dict | None = None,
    ) -> EditResult:
        """迭代修圖（用於 image thread 多輪對話）。"""
