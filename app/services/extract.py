"""
Extract câu hỏi từ PDF bằng Gemini (đọc PDF trực tiếp, không cần OCR riêng).

Staff upload PDF (đề thi/tài liệu ≤20 trang) -> Gemini đọc -> trả list câu hỏi
{content, solution, problem_type, chapter, page}. Dùng response_schema để LaTeX
(backslash) được escape đúng, không vỡ JSON.

KHÔNG embed ở đây — embed là bước riêng (BE gọi /api/v1/embed sau khi lưu câu).

Trước tiên chấp nhận schema không có quản lí chapter riêng
"""
from __future__ import annotations

import base64

from google import genai
from google.genai import types

from ..models.extract import ExtractPdfResponse, ExtractedQuestion

MODEL = "gemini-2.5-flash"   # flash (không lite) — đọc PDF nhiều câu cần hiểu layout tốt

# Danh sách chapter chuẩn Bộ GD — đồng bộ với classifier.py để chapter nhất quán.
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
- content: NGUYÊN VĂN đề bài (gồm phương án A/B/C/D nếu trắc nghiệm). Công thức để trong LaTeX $...$.
- solution: lời giải/đáp án nếu PDF có (không có thì để rỗng).
- problem_type: "tu_luan" | "trac_nghiem" | "dien_so".
- chapter: chọn ĐÚNG 1 tên từ danh sách sau nếu khớp, không khớp để rỗng:
  {_CHAPTERS}
- page: số trang chứa câu đó (1-based).

QUY TẮC:
- Giữ NGUYÊN VĂN, KHÔNG viết lại, KHÔNG bịa thêm câu không có trong PDF.
- Tách riêng từng câu; không gộp nhiều câu làm một."""

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
        },
    ),
)


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
        questions = [
            ExtractedQuestion(
                content=(q.get("content") or "").strip(),
                solution=(q.get("solution") or "").strip() or None,
                problem_type=(q.get("problem_type") or "").strip() or None,
                chapter=(q.get("chapter") or "").strip() or None,
                page=q.get("page"),
            )
            for q in raw if (q.get("content") or "").strip()
        ]
        return ExtractPdfResponse(total=len(questions), questions=questions)
    except Exception as e:
        return ExtractPdfResponse(total=0, questions=[], error=f"{type(e).__name__}: {e}")


async def extract_pdf_base64(client: genai.Client, pdf_base64: str) -> ExtractPdfResponse:
    return await extract_pdf(client, base64.b64decode(pdf_base64))
