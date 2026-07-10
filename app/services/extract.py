"""
Extract câu hỏi từ PDF bằng Gemini (đọc PDF trực tiếp).

Staff upload PDF -> Gemini đọc -> list câu {content, solution, problem_type,
chapter, page, has_figure}.

CROP HÌNH: KHÔNG để Gemini đoán toạ độ pixel (LLM rất kém việc này -> crop lệch).
Thay vào đó dùng PyMuPDF dò VÙNG HÌNH THẬT trên trang (ảnh raster qua
get_image_rects, bảng qua find_tables, cụm vector qua get_drawings) rồi gán cho
câu có has_figure theo thứ tự đọc (y) trên trang. Toạ độ đọc thẳng từ cấu trúc
PDF nên chính xác tuyệt đối. (Đây là cách MinerU/Docling/PyMuPDF4LLM làm.)

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
- page: số trang chứa câu (0-based, trang đầu = 0). BẮT BUỘC đúng trang.
- has_figure: true nếu câu có HÌNH ẢNH đi kèm (bảng biến thiên, đồ thị, hình vẽ,
  hình học); false nếu chỉ có chữ + công thức. KHÔNG tự đoán toạ độ — chỉ cần cờ này.

QUY TẮC:
- Giữ NGUYÊN VĂN, KHÔNG bịa câu không có trong PDF.
- Trả các câu THEO ĐÚNG THỨ TỰ xuất hiện trong PDF (trên xuống dưới, trái qua phải).
- Chỉ đặt has_figure=true cho câu THẬT SỰ có hình vẽ (không tính công thức LaTeX)."""

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
            "has_figure": types.Schema(type=types.Type.BOOLEAN),
        },
    ),
)


def _cluster_rects(rects: list, gap: float = 12.0) -> list:
    """Gom các rect gần nhau (giao hoặc cách < gap px) thành cụm lớn — dùng để
    ghép các nét vector rời rạc (đồ thị, hình vẽ) thành 1 vùng hình."""
    boxes = [fitz.Rect(r) for r in rects if fitz.Rect(r).width > 3 and fitz.Rect(r).height > 3]
    merged = True
    while merged:
        merged = False
        out: list = []
        while boxes:
            b = boxes.pop()
            grew = True
            while grew:
                grew = False
                rest: list = []
                for o in boxes:
                    be = fitz.Rect(b)
                    be.x0 -= gap; be.y0 -= gap; be.x1 += gap; be.y1 += gap
                    if be.intersects(o):
                        b |= o
                        grew = True
                        merged = True
                    else:
                        rest.append(o)
                boxes = rest
            out.append(b)
        boxes = out
    return boxes


def _detect_figure_regions(page: fitz.Page) -> list:
    """Vùng hình THẬT trên trang (toạ độ đọc thẳng từ PDF, không đoán):
    ảnh raster + bảng + cụm vector drawing đủ lớn. Trả list Rect theo thứ tự đọc."""
    regions: list = []
    # 1. Ảnh raster nhúng (bbox chính xác tuyệt đối).
    for im in page.get_images():
        regions += list(page.get_image_rects(im[0]))
    # 2. Bảng (bảng biến thiên kẻ line).
    try:
        for t in page.find_tables().tables:
            regions.append(fitz.Rect(t.bbox))
    except Exception:
        pass
    # 3. Vector drawings -> cluster (đồ thị, hình vẽ tay). Bỏ cụm quá nhỏ (gạch chân, nét lẻ).
    clusters = _cluster_rects([d["rect"] for d in page.get_drawings()])
    for c in clusters:
        if c.width >= 60 and c.height >= 40:
            regions.append(c)
    # Dedup: bỏ region nằm gọn (>80% diện tích) trong region lớn hơn.
    regions = sorted(regions, key=lambda r: -r.get_area())
    keep: list = []
    for r in regions:
        if r.get_area() > 0 and not any((r & k).get_area() > 0.8 * r.get_area() for k in keep):
            keep.append(r)
    # Sắp theo thứ tự đọc (trên xuống, trái qua phải).
    keep.sort(key=lambda r: (round(r.y0), r.x0))
    return keep


def _crop_rect(page: fitz.Page, rect: fitz.Rect) -> str | None:
    """Render 1 vùng thành PNG base64, nới nhẹ 4px cho đỡ cắt sát."""
    try:
        r = fitz.Rect(rect.x0 - 4, rect.y0 - 4, rect.x1 + 4, rect.y1 + 4) & page.rect
        if r.is_empty or r.width < 8 or r.height < 8:
            return None
        pix = page.get_pixmap(clip=r, dpi=150)
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
        # Dò sẵn vùng hình thật cho từng trang (1 lần/trang).
        page_regions: dict[int, list] = {}

        def regions_for(pno: int) -> list:
            if pno not in page_regions:
                page_regions[pno] = _detect_figure_regions(doc[pno])
            return page_regions[pno]

        # Con trỏ vùng hình đã dùng trên mỗi trang -> nhiều câu có hình cùng trang
        # nhận vùng theo thứ tự đọc.
        used: dict[int, int] = {}

        def resolve_page(pno: int) -> int | None:
            """Trang thật còn vùng hình chưa dùng. Gemini hay trả số trang theo
            NHÃN in trên tài liệu (vd 'Page 8') thay vì index 0-based của file đã
            tách -> nếu ngoài range hoặc hết hình, dò trang khác còn hình."""
            if 0 <= pno < len(doc) and used.get(pno, 0) < len(regions_for(pno)):
                return pno
            for p in range(len(doc)):
                if used.get(p, 0) < len(regions_for(p)):
                    return p
            return None

        questions = []
        for q in raw:
            content = (q.get("content") or "").strip()
            if not content:
                continue
            images = []
            if q.get("has_figure"):
                pno = resolve_page(int(q.get("page") or 0))
                if pno is not None:
                    regs = regions_for(pno)
                    idx = used[pno] = used.get(pno, 0)
                    png_b64 = _crop_rect(doc[pno], regs[idx])
                    if png_b64:
                        images.append(png_b64)
                    used[pno] = idx + 1
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
