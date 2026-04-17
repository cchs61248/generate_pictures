"""
Extract plain text from common document formats for ecommerce pipeline LLM prompts.
"""
import os

from core.app_logging import get_backend_logger

_MAX_CHARS = 10_000
logger = get_backend_logger("services.document_reader")


def _truncate(text: str) -> str:
    if len(text) <= _MAX_CHARS:
        return text
    return text[:_MAX_CHARS] + "\n\n...(truncated)"


def extract_text(file_path: str) -> str:
    """
    Read text by file extension. Unsupported or failed reads return empty string.
    Output is truncated to _MAX_CHARS characters.
    """
    if not file_path or not os.path.isfile(file_path):
        logger.debug("[document_reader] file not found: %s", file_path)
        return ""

    ext = os.path.splitext(file_path)[1].lower()
    logger.debug("[document_reader] extract start | path=%s ext=%s", file_path, ext)

    try:
        if ext in (".txt", ".md"):
            with open(file_path, encoding="utf-8", errors="replace") as f:
                text = _truncate(f.read())
                logger.debug("[document_reader] extract done | ext=%s chars=%d", ext, len(text))
                return text

        if ext == ".pdf":
            from pypdf import PdfReader

            reader = PdfReader(file_path)
            parts: list[str] = []
            for page in reader.pages:
                t = page.extract_text() or ""
                if t.strip():
                    parts.append(t)
            text = _truncate("\n".join(parts))
            logger.debug("[document_reader] extract done | ext=%s chars=%d", ext, len(text))
            return text

        if ext == ".docx":
            import docx

            doc = docx.Document(file_path)
            paras = [p.text for p in doc.paragraphs if p.text and p.text.strip()]
            text = _truncate("\n".join(paras))
            logger.debug("[document_reader] extract done | ext=%s chars=%d", ext, len(text))
            return text

        logger.debug("[document_reader] unsupported extension: %s", ext)
        return ""
    except Exception as exc:
        logger.warning("[document_reader] extract failed | path=%s err=%s", file_path, exc)
        return ""
