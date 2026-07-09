"""
Extract câu hỏi từ PDF bằng Gemini (đọc PDF trực tiếp).

Staff upload PDF -> Gemini đọc -> list câu {content, solution, problem_type,
chapter, page, figures}. figures = vùng chứa hình (bảng biến thiên/đồ thị) do
Gemini định vị; PyMuPDF render vùng đó thành PNG (base64) để BE upload + gắn
image_urls vào câu.

Dùng response_schema để LaTeX (backslash) escape đúng, không vỡ JSON.
KHÔNG embed ở đây.
"""
from __future__ import annotations

import base64

import fitz  # PyMuPDF
from google import genai
from google.genai import types

from ..models.extract import ExtractPdfResponse, ExtractedQuestion

MODEL = "gemini-2.5-flash"

_CHAPTERS = (
    "bat_phuong_trinh_bac_nhat_hai_an, can_bac_hai, cap_so_cong, da_thuc, dao_ham, "
    "day_so_cap_so_cong_cap_so_nhan, gia_tri_luong_giac, gioi_han_ham_so, "
    "goc_luong_giac_va_gia_tri_luong_giac, ham_so_luong_giac, ham_so_mu_va_ham_so_logarit, "
    "he_thuc_luong_trong_tam_giac, khao_sat_ham_so, menh_de, menh_de_tap_hop, nguyen_ham, "
    "phuong_phap_toa_do_trong_khong_gian, phuong_trinh_va_he_phuong_trinh_bac_nhat_hai_an, "
    "so_phuc, tich_phan, to_hop_xac_suat, ung_dung_dao_ham, ung_dung_tich_phan, vecto, "
    "xac_suat_co_dieu_kien"
)

_PROMPT = f"""Đọc toàn bộ PDF này (đề thi/tài liệu Toán) và TRÍCH XUẤT từng câu hỏi.
Với MỖI câu hỏi, trả về:
- content: NGUYÊN VĂN đề bài (gồm phương án A/B/C/D nếu trắc nghiệm). Công thức LaTeX $...$.
- solution: lời giải/đáp án nếu PDF có (không có thì rỗng).
- problem_type: "tu_luan" | "trac_nghiem" | "dien_so".
- chapter: chọn ĐÚNG 1 tên từ danh sách nếu khớp, không khớp để rỗng:
  {_CHAPTERS}
- page: số trang chứa câu (0-based, trang đầu = 0).
- figures: danh sách vùng chứa HÌNH ẢNH (bảng biến thiên, đồ thị, hình vẽ) của câu
  đó. Mỗi figure có tọa độ theo hệ pixel của trang PDF (gốc trên-trái):
  {{"page": số trang 0-based, "x": trái, "y": trên, "width": rộng, "height": cao,
    "page_width": chiều rộng trang, "page_height": chiều cao trang}}.
  Câu KHÔNG có hình -> figures = [].

QUY TẮC:
- Giữ NGUYÊN VĂN, KHÔNG bịa câu không có trong PDF.
- Tách riêng từng câu; chỉ đánh figures cho câu THẬT SỰ có hình vẽ."""

_FIGURE = types.Schema(
    type=types.Type.OBJECT,
    properties={
        "page": types.Schema(type=types.Type.INTEGER),
        "x": types.Schema(type=types.Type.NUMBER),
        "y": types.Schema(type=types.Type.NUMBER),
        "width": types.Schema(type=types.Type.NUMBER),
        "height": types.Schema(type=types.Type.NUMBER),
        "page_width": types.Schema(type=types.Type.NUMBER),
        "page_height": types.Schema(type=types.Type.NUMBER),
    },
)

_SCHEMA = types.Schema(
    type=types.Type.ARRAY,
    items=types.Schema(
        type=types.Type.OBJECT,
        required=["content"],
        properties={
            "content": types.Schema(type=types.Type.STRING),
            "solution": types.Schema(type=types.Type.STRING),
            "problem_type": types.Schema(type=types.Type.STRING),
            "chapter": types.Schema(type=types.Type.STRING),
            "page": types.Schema(type=types.Type.INTEGER),
            "figures": types.Schema(type=types.Type.ARRAY, items=_FIGURE),
        },
    ),
)


def _crop_figure(doc: fitz.Document, fig: dict) -> str | None:
    """Render vùng figure thành PNG base64. Tọa độ Gemini theo page_width/height
    của nó -> scale về tọa độ thật của trang PDF."""
    try:
        page = doc[int(fig.get("page", 0))]
        pw = float(fig.get("page_width") or page.rect.width)
        ph = float(fig.get("page_height") or page.rect.height)
        sx = page.rect.width / pw if pw else 1.0
        sy = page.rect.height / ph if ph else 1.0
        x0 = float(fig["x"]) * sx
        y0 = float(fig["y"]) * sy
        x1 = (float(fig["x"]) + float(fig["width"])) * sx
        y1 = (float(fig["y"]) + float(fig["height"])) * sy
        # nới nhẹ 4px cho đỡ cắt sát, kẹp trong trang
        rect = fitz.Rect(x0 - 4, y0 - 4, x1 + 4, y1 + 4) & page.rect
        if rect.is_empty or rect.width < 8 or rect.height < 8:
            return None
        pix = page.get_pixmap(clip=rect, dpi=150)
        return base64.b64encode(pix.tobytes("png")).decode()
    except Exception:
        return None


async def extract_pdf(client: genai.Client, pdf_bytes: bytes) -> ExtractPdfResponse:
    import json
    try:
        response = client.models.generate_content(
            model=MODEL,
            contents=[
                types.Content(parts=[
                    types.Part.from_bytes(data=pdf_bytes, mime_type="application/pdf"),
                    types.Part.from_text(text=_PROMPT),
                ])
            ],
            config=types.GenerateContentConfig(
                temperature=0.0,
                response_mime_type="application/json",
                response_schema=_SCHEMA,
            ),
        )
        raw = json.loads(response.text)

        doc = fitz.open(stream=pdf_bytes, filetype="pdf")
        questions = []
        for q in raw:
            content = (q.get("content") or "").strip()
            if not content:
                continue
            images = []
            for fig in (q.get("figures") or []):
                png_b64 = _crop_figure(doc, fig)
                if png_b64:
                    images.append(png_b64)
            questions.append(ExtractedQuestion(
                content=content,
                solution=(q.get("solution") or "").strip() or None,
                problem_type=(q.get("problem_type") or "").strip() or None,
                chapter=(q.get("chapter") or "").strip() or None,
                page=q.get("page"),
                images=images,
            ))
        doc.close()
        return ExtractPdfResponse(total=len(questions), questions=questions)
    except Exception as e:
        return ExtractPdfResponse(total=0, questions=[], error=f"{type(e).__name__}: {e}")


async def extract_pdf_base64(client: genai.Client, pdf_base64: str) -> ExtractPdfResponse:
    return await extract_pdf(client, base64.b64decode(pdf_base64))
