"""
Extract plain text from common document formats for ecommerce pipeline LLM prompts.
"""
import os

_MAX_CHARS = 10_000


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
        return ""

    ext = os.path.splitext(file_path)[1].lower()

    try:
        if ext in (".txt", ".md"):
            with open(file_path, encoding="utf-8", errors="replace") as f:
                return _truncate(f.read())

        if ext == ".pdf":
            from pypdf import PdfReader

            reader = PdfReader(file_path)
            parts: list[str] = []
            for page in reader.pages:
                t = page.extract_text() or ""
                if t.strip():
                    parts.append(t)
            return _truncate("\n".join(parts))

        if ext == ".docx":
            import docx

            doc = docx.Document(file_path)
            paras = [p.text for p in doc.paragraphs if p.text and p.text.strip()]
            return _truncate("\n".join(paras))

        return ""
    except Exception:
        return ""
