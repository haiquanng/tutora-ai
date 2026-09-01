"""
Unit test cho classroom/ — bài tập nhanh trong buổi học.
Thuần, không gọi Gemini/DB, tất định.
"""
import asyncio

import fitz
import pytest

from app.services.classroom.classify import check_relevance
from app.services.classroom.extract import extract_pdf_text, detect_kind
from app.services.classroom.generate import (
    build_documents_block,
    _normalize,
    _fix_line_breaks,
    _PROMPT,
    MAX_CONTEXT_CHARS,
)


# Trích xuất
def _make_pdf(pages: list[str]) -> bytes:
    doc = fitz.open()
    for text in pages:
        page = doc.new_page()
        page.insert_text((60, 100), text, fontsize=12)
    data = doc.tobytes()
    doc.close()
    return data


def test_pdf_co_moc_trang():
    """Mốc '[trang N]' là thứ cho phép AI trích dẫn nguồn — mất là hỏng tính năng."""
    text, page_count = extract_pdf_text(_make_pdf([
        "Chuong 1: Dao ham co ban va cac quy tac tinh",
        "Chuong 2: Quy tac chuoi va ung dung thuc te",
    ]))

    assert page_count == 2
    assert "[trang 1]" in text
    assert "[trang 2]" in text
    # Đúng thứ tự trang, không đảo.
    assert text.index("[trang 1]") < text.index("[trang 2]")


def test_pdf_bo_trang_trong():
    """Trang scan/không text -> không dựng mốc trang rỗng làm nhiễu prompt."""
    text, page_count = extract_pdf_text(_make_pdf([
        "Noi dung that su co y nghia o trang nay",
        "",
    ]))

    assert page_count == 2
    assert "[trang 1]" in text
    assert "[trang 2]" not in text


def test_detect_kind():
    assert detect_kind("slide.pdf", None) == "pdf"
    assert detect_kind("bang.png", "image/png") == "image"
    # Tên file không đuôi nhưng content-type là pdf.
    assert detect_kind("upload", "application/pdf") == "pdf"


# Dựng khối tài liệu
def test_nhan_nguon_mang_material_id_that():
    """Nhãn phải có material_id THẬT: đánh số 1,2,3 thì AI trả số thứ tự -> BE map sai."""
    block, truncated = build_documents_block([
        {"material_id": 10, "title": "Slide chương 1", "full_text": "[trang 1]\nA"},
        {"material_id": 25, "title": "Đề cương", "full_text": "[trang 4]\nB"},
    ])

    assert "id=10" in block and "id=25" in block
    assert "Slide chương 1" in block and "Đề cương" in block
    assert not truncated


def test_cat_bot_khi_qua_dai():
    """Tài liệu vượt trần thì cắt và BÁO, không im lặng bỏ."""
    block, truncated = build_documents_block([
        {"material_id": 1, "title": "Giáo trình dày", "full_text": "x" * (MAX_CONTEXT_CHARS + 5000)},
    ])

    assert truncated
    assert len(block) <= MAX_CONTEXT_CHARS + 200  # trừ phần header


# Lọc câu AI sinh
def test_giu_cau_hop_le():
    result = _normalize({"title": "Ôn tập", "questions": [{
        "format": "mc",
        "content": "Đạo hàm của $x^2$?",
        "options": [{"key": "A", "text": "$2x$"}, {"key": "B", "text": "$x$"}],
        "correct_answer": "A",
        "explanation": "Quy tắc luỹ thừa.",
        "source_material_id": 10,
        "source_page": 12,
    }]})

    q = result["questions"][0]
    assert q["correct_answer"] == "A"
    assert q["source_page"] == 12
    assert result["title"] == "Ôn tập"


@pytest.mark.parametrize("bad_question, ly_do", [
    (
        {"format": "mc", "content": "x", "options": [{"key": "A", "text": "1"}], "correct_answer": "A"},
        "chỉ 1 phương án",
    ),
    (
        {"format": "mc", "content": "x", "options": [{"key": "A", "text": "1"}, {"key": "B", "text": "2"}]},
        "thiếu correct_answer",
    ),
    (
        {"format": "mc", "content": "x", "options": [{"key": "A", "text": "1"}, {"key": "B", "text": "2"}],
         "correct_answer": "Z"},
        "đáp án trỏ phương án không tồn tại",
    ),
    (
        {"format": "mc", "content": "   ", "options": [{"key": "A", "text": "1"}, {"key": "B", "text": "2"}],
         "correct_answer": "A"},
        "đề rỗng",
    ),
])
def test_loai_cau_hong(bad_question, ly_do):
    """Câu hỏng lọt xuống là gia sư phải dọn tay giữa lúc đang dạy, hoặc DB chặn."""
    result = _normalize({"title": "T", "questions": [bad_question]})
    assert result["questions"] == [], ly_do


def test_tu_luan_bi_go_dap_an():
    """Tự luận có correct_answer là vô nghĩa — DB cũng không cho chấm tự động."""
    result = _normalize({"title": "T", "questions": [{
        "format": "essay",
        "content": "Trình bày quy tắc chuỗi.",
        "options": [{"key": "A", "text": "1"}],
        "correct_answer": "A",
    }]})

    q = result["questions"][0]
    assert q["correct_answer"] is None
    assert q["options"] is None


def test_title_rong_co_mac_dinh():
    result = _normalize({"title": "  ", "questions": []})
    assert result["title"]


# Ngăn cách dòng trong hệ phương trình
def test_va_ngan_cach_dong_bi_mat_escape():
    """Model hay trả 1 gạch chéo thay vì 2 -> KaTeX coi là dấu cách, hai phương
    trình dồn thành MỘT dòng và học sinh đọc sai đề."""
    bad = r"$\begin{cases} 3x - 2y = 11 \ x + 2y = 9 \end{cases}$"
    assert r"11 \\ x" in _fix_line_breaks(bad)


def test_giu_nguyen_khi_da_dung():
    good = r"$\begin{cases} 3x - 2y = 11 \\ x + 2y = 9 \end{cases}$"
    assert _fix_line_breaks(good) == good


def test_va_khi_khong_co_khoang_trang():
    """Model có thể viết liền không dấu cách — bản vá đầu tiên chỉ bắt trường hợp
    có khoảng trắng nên lọt hết ca này."""
    bad = r"$\begin{cases}3x-2y=11\x+2y=9\end{cases}$"
    assert r"11\\x" in _fix_line_breaks(bad)


def test_giu_lenh_latex_ben_trong_cases():
    """\frac nằm TRONG cases vẫn phải giữ nguyên, chỉ ngăn cách dòng mới được nâng."""
    src = r"$\begin{cases}\frac{1}{2}=a\b=2\end{cases}$"
    out = _fix_line_breaks(src)
    assert r"\frac{1}{2}" in out
    assert r"=a\\b=2" in out


def test_khong_dung_toi_lenh_latex_khac():
    """\frac, \sqrt... cũng bắt đầu bằng 1 gạch chéo — không được đụng vào."""
    src = r"$\frac{1}{2} \cdot \sqrt{x}$"
    assert _fix_line_breaks(src) == src


def test_va_ca_trong_option_va_explanation():
    result = _normalize({"title": "T", "questions": [{
        "format": "mc",
        "content": r"$\begin{cases} x = 1 \ y = 2 \end{cases}$",
        "options": [
            {"key": "A", "text": r"$\begin{aligned} a = 1 \ b = 2 \end{aligned}$"},
            {"key": "B", "text": "khác"},
        ],
        "correct_answer": "A",
        "explanation": r"$\begin{cases} m \ n \end{cases}$",
    }]})

    q = result["questions"][0]
    assert r"\\" in q["content"]
    assert r"\\" in q["options"][0]["text"]
    assert r"\\" in q["explanation"]


# Dựng prompt
def test_prompt_khong_dung_str_format():
    """Prompt chứa "{cases}" trong ví dụ LaTeX. Nếu ai đó đổi lại sang .format()
    thì Python coi đó là placeholder và ném KeyError('cases') -> KHÔNG sinh được
    câu nào, người dùng chỉ thấy lỗi 400 chung chung."""
    assert "{cases}" in _PROMPT

    with pytest.raises(KeyError):
        _PROMPT.format(prompt="x", documents="y")

    # Cách thay thế đang dùng phải chạy được và điền đủ 2 chỗ.
    built = _PROMPT.replace("<<PROMPT>>", "YÊU CẦU").replace("<<DOCUMENTS>>", "TÀI LIỆU")
    assert "YÊU CẦU" in built and "TÀI LIỆU" in built
    assert "<<PROMPT>>" not in built and "<<DOCUMENTS>>" not in built


# Đối chiếu môn học
def test_tai_lieu_rong_bi_tu_choi():
    """Không có chữ thì không thể là học liệu — chặn luôn, khỏi gọi model."""
    result = asyncio.run(check_relevance(None, "   ", "Toán"))
    assert result["relevant"] is False
    assert result["reason"]


def test_loi_goi_model_thi_cho_qua():
    """Dịch vụ AI trục trặc KHÔNG được biến thành chặn gia sư tải tài liệu."""

    class Broken:
        class models:
            @staticmethod
            def generate_content(**_):
                raise RuntimeError("Gemini down")

    result = asyncio.run(check_relevance(Broken(), "Học liệu môn Toán", "Toán"))
    assert result["relevant"] is True
