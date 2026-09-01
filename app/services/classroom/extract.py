"""
Trích TOÀN VĂN tài liệu học tập (PDF / ảnh) cho tính năng bài tập trong buổi học.
"""
from __future__ import annotations

import asyncio

from google.genai import types

from ..homework.ocr import _detect_mime
from ..telemetry.usage import track

_MODEL = "gemini-2.5-flash"

# Ảnh chụp bảng/vở thì OCR; PDF đọc text trực tiếp nhanh và chính xác hơn nhiều.
_IMAGE_OCR_PROMPT = (
    "Đọc và trích xuất NGUYÊN VĂN toàn bộ nội dung trong ảnh tài liệu học tập này.\n"
    "- Giữ nguyên cấu trúc: tiêu đề, đề mục, danh sách, bảng.\n"
    "- Chuyển mọi công thức toán sang LaTeX kẹp trong $...$.\n"
    "- KHÔNG tóm tắt, KHÔNG thêm lời dẫn, KHÔNG giải thích.\n"
    "Nếu ảnh không có nội dung chữ đọc được, trả về đúng một dòng: NO_CONTENT"
)

NO_CONTENT = "NO_CONTENT"

# Trang PDF không có text (trang scan/ảnh thuần) -> bỏ qua, không dựng mốc trang rỗng.
_MIN_PAGE_CHARS = 10


def extract_pdf_text(file_bytes: bytes) -> tuple[str, int]:
    """PDF -> (toàn văn có mốc trang, số trang). Đọc text layer, KHÔNG OCR."""
    import fitz  # PyMuPDF

    parts: list[str] = []
    with fitz.open(stream=file_bytes, filetype="pdf") as doc:
        page_count = doc.page_count
        for index, page in enumerate(doc, start=1):
            text = page.get_text("text").strip()
            if len(text) >= _MIN_PAGE_CHARS:
                parts.append(f"[trang {index}]\n{text}")

    return "\n\n".join(parts), page_count


async def extract_image_text(gemini, file_bytes: bytes) -> str:
    """Ảnh -> toàn văn qua Gemini. Ảnh là 1 trang nên gắn mốc [trang 1]."""
    mime = _detect_mime(file_bytes)

    with track("classroom_extract_image", _MODEL) as _t:
        response = _t.done(await asyncio.to_thread(
            gemini.models.generate_content,
            model=_MODEL,
            contents=[
                types.Part.from_bytes(data=file_bytes, mime_type=mime),
                _IMAGE_OCR_PROMPT,
            ],
        ))

    text = (response.text or "").strip()
    if not text or text.startswith(NO_CONTENT):
        return ""
    return f"[trang 1]\n{text}"


def detect_kind(file_name: str, content_type: str | None) -> str:
    """'pdf' | 'image'. Tài liệu lớp chỉ nhận 2 loại này."""
    name = file_name.lower()
    if name.endswith(".pdf") or (content_type or "").startswith("application/pdf"):
        return "pdf"
    return "image"
