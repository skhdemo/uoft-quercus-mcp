"""Tests for Quercus text extraction helpers."""

from __future__ import annotations

from pathlib import Path

from uoft_quercus_mcp.quercus.extract import extract_text, safe_filename

FIXTURES = Path(__file__).parent / "fixtures" / "quercus"


def test_extract_txt() -> None:
    data = (FIXTURES / "sample.txt").read_bytes()
    result = extract_text(data, content_type="text/plain", filename="sample.txt")
    assert result.text == "hello quercus text fixture\n"
    assert result.truncated is False


def test_extract_pdf_nonempty() -> None:
    data = (FIXTURES / "sample.pdf").read_bytes()
    result = extract_text(data, content_type="application/pdf", filename="sample.pdf")
    assert result.text is not None
    assert "Hello Quercus PDF" in result.text


def test_extract_image_unsupported() -> None:
    result = extract_text(
        b"\x89PNG\r\n",
        content_type="image/png",
        filename="scan.png",
    )
    assert result.text is None
    assert any("OCR" in warning for warning in result.warnings)


def test_extract_truncation() -> None:
    data = ("x" * 100).encode()
    result = extract_text(
        data,
        content_type="text/plain",
        filename="big.txt",
        max_text_chars=20,
    )
    assert result.text is not None
    assert len(result.text) == 20
    assert result.truncated is True


def test_safe_filename_strips_path() -> None:
    assert safe_filename("../evil/quiz.pdf", file_id=801) == "801-quiz.pdf"
