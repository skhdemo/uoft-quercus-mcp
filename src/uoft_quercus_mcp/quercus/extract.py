"""Best-effort text extraction from Quercus file bytes."""

from __future__ import annotations

import io
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class ExtractResult:
    text: str | None
    truncated: bool = False
    warnings: list[str] = field(default_factory=list)


def _truncate(text: str, max_chars: int) -> tuple[str, bool]:
    if max_chars <= 0 or len(text) <= max_chars:
        return text, False
    return text[:max_chars], True


def _looks_like(filename: str | None, *suffixes: str) -> bool:
    if not filename:
        return False
    lower = filename.lower()
    return any(lower.endswith(suffix) for suffix in suffixes)


def extract_text(
    data: bytes,
    *,
    content_type: str | None,
    filename: str | None,
    max_text_chars: int = 200_000,
) -> ExtractResult:
    """Return extracted text + warnings. Unsupported types yield text=None."""
    ctype = (content_type or "").lower().split(";", 1)[0].strip()
    name = filename or ""

    if ctype == "application/pdf" or _looks_like(name, ".pdf"):
        return _extract_pdf(data, max_text_chars=max_text_chars)

    if (
        ctype
        == "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
        or _looks_like(name, ".docx")
    ):
        return _extract_docx(data, max_text_chars=max_text_chars)

    if (
        ctype.startswith("text/")
        or _looks_like(name, ".txt", ".md", ".markdown", ".csv", ".json", ".log")
        or ctype in {"application/json", "application/csv", "application/xml"}
    ):
        text = data.decode("utf-8", errors="replace")
        truncated_text, truncated = _truncate(text, max_text_chars)
        return ExtractResult(text=truncated_text, truncated=truncated)

    if ctype.startswith("image/") or _looks_like(
        name, ".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp"
    ):
        return ExtractResult(
            text=None,
            warnings=[
                "Image files are downloaded only; OCR is not supported in Phase 2."
            ],
        )

    return ExtractResult(
        text=None,
        warnings=["Unsupported content type for text extraction; use mode=download."],
    )


def _extract_pdf(data: bytes, *, max_text_chars: int) -> ExtractResult:
    try:
        from pypdf import PdfReader
    except ImportError:
        return ExtractResult(
            text=None,
            warnings=["pypdf is not installed; cannot extract PDF text."],
        )
    try:
        reader = PdfReader(io.BytesIO(data))
        parts: list[str] = []
        for page in reader.pages:
            parts.append(page.extract_text() or "")
        text = "\n".join(parts).strip()
    except Exception as exc:  # noqa: BLE001 - soft failure for tool UX
        return ExtractResult(
            text=None,
            warnings=[f"PDF text extraction failed: {exc.__class__.__name__}."],
        )
    if not text:
        return ExtractResult(
            text=None,
            warnings=[
                "PDF contained no extractable text "
                "(may be a scanned image; OCR is not supported)."
            ],
        )
    truncated_text, truncated = _truncate(text, max_text_chars)
    return ExtractResult(text=truncated_text, truncated=truncated)


def _extract_docx(data: bytes, *, max_text_chars: int) -> ExtractResult:
    try:
        from docx import Document
    except ImportError:
        return ExtractResult(
            text=None,
            warnings=["python-docx is not installed; cannot extract DOCX text."],
        )
    try:
        document = Document(io.BytesIO(data))
        text = "\n".join(p.text for p in document.paragraphs if p.text).strip()
    except Exception as exc:  # noqa: BLE001 - soft failure for tool UX
        return ExtractResult(
            text=None,
            warnings=[f"DOCX text extraction failed: {exc.__class__.__name__}."],
        )
    truncated_text, truncated = _truncate(text, max_text_chars)
    return ExtractResult(text=truncated_text or None, truncated=truncated)


def safe_filename(name: str | None, *, file_id: int | str) -> str:
    """Return a path-traversal-safe filename with a file-id prefix."""
    base = Path(name or f"file-{file_id}").name.strip() or f"file-{file_id}"
    return f"{file_id}-{base}"
