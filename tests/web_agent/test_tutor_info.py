"""
Test intent tutor_info — hỏi VỀ một gia sư đã được gợi ý.

Hai bài toán chuẩn: coreference resolution (thầy A / người đầu tiên → tutor_id nào) và
grounding (trả lời từ hồ sơ THẬT, không bịa). shown_tutors = entity memory do FE giữ.

_resolve là hàm THUẦN nên test được tất định, không phụ thuộc LLM.
"""
from __future__ import annotations

import asyncio

from app.services.web_agent.handlers.tutor_info import _resolve, _strip_accents
from app.services.web_agent.agent import web_chat
from app.services.web_agent.schemas import WebChatRequest
from app.models.schemas import ShownTutor


SHOWN = [ShownTutor(tutor_id="a", name="Lê Tuấn Tú"),
         ShownTutor(tutor_id="b", name="Nguyễn Thị Hương")]


def test_strip_accents_xu_ly_chu_d():
    """đ/Đ là ký tự độc lập (U+0111), NFD không tách được → phải map riêng sang 'd'."""
    assert _strip_accents("đầu tiên") == "dau tien"
    assert _strip_accents("Đỗ Quang Huy") == "do quang huy"


def test_resolve_theo_ten_va_ten_goi_tat():
    assert _resolve("cho tôi biết về Lê Tuấn Tú", SHOWN).tutor_id == "a"
    # Người Việt gọi tắt bằng 1 âm tiết cuối.
    assert _resolve("thầy Tú có kinh nghiệm không", SHOWN).tutor_id == "a"
    assert _resolve("cô Hương dạy sao", SHOWN).tutor_id == "b"
    # Gõ không dấu vẫn phải ra.
    assert _resolve("le tuan tu the nao", SHOWN).tutor_id == "a"


def test_resolve_theo_thu_tu():
    assert _resolve("người đầu tiên học phí bao nhiêu?", SHOWN).tutor_id == "a"
    assert _resolve("người thứ nhất", SHOWN).tutor_id == "a"
    assert _resolve("bạn thứ 2 thế nào", SHOWN).tutor_id == "b"


def test_repair_turn_khi_khong_ro_la_ai():
    """Mơ hồ → trả None để handler HỎI LẠI. Đoán bừa ("lệch âm thầm") là lỗi tệ nhất."""
    assert _resolve("người đó dạy sao", SHOWN) is None
    assert _resolve("gia sư nào cũng được", SHOWN) is None
    # Chưa hiện ai mà đã hỏi → cũng phải hỏi lại.
    assert _resolve("thầy Tú thế nào", []) is None


def test_chi_co_mot_nguoi_thi_moi_cach_goi_deu_tro_ve_nguoi_do():
    one = [ShownTutor(tutor_id="x", name="Trần Văn A")]
    assert _resolve("thầy đó dạy sao", one).tutor_id == "x"


def test_khong_bia_gia_su_ngoai_danh_sach():
    """Tập trắng: tên KHÔNG có trong shown_tutors thì không được khớp bừa sang người khác."""
    assert _resolve("cho tôi biết về thầy Phạm Văn Đức", SHOWN) is None


def test_end_to_end_hoi_ve_gia_su_khong_ban_lai_card():
    """tutor_info phải trả lời bằng chữ, KHÔNG kèm card (chống spam card)."""
    r = asyncio.run(web_chat(WebChatRequest(
        history=[{"role": "user", "content": "Tìm gia sư Toán lớp 12"},
                 {"role": "assistant", "content": "Mình tìm được 2 gia sư phù hợp:"}],
        message="Cho tôi biết thêm về Lê Tuấn Tú",
        shown_tutors=SHOWN,
    )))
    assert r.intent == "tutor_info"
    assert r.cards == [], "hỏi thông tin thì KHÔNG được bắn lại card"
    assert r.reply.strip()
    # Entity memory phải được echo lại để lượt sau vẫn hiểu "thầy A".
    assert [t.tutor_id for t in r.shown_tutors] == ["a", "b"]


def test_discourse_focus_khong_hoi_lai_khi_dang_tiep_mach():
    """Bug thật: hỏi về thầy Nam xong, lượt sau "thầy có bằng cấp gì" lại bị hỏi
    "bạn muốn hỏi ai?" — vì chỉ nhìn tin nhắn hiện tại, không nhìn hội thoại."""
    shown = [ShownTutor(tutor_id="a", name="Nguyễn Hoàng Nam"),
             ShownTutor(tutor_id="b", name="Lê Tuấn Tú")]
    hist = [{"role": "user", "content": "giờ rảnh của thầy Nguyễn Hoàng Nam?"},
            {"role": "assistant", "content": "Thầy rảnh Chủ Nhật 18:00-21:00."}]

    # Câu KHÔNG nêu tên nhưng đang tiếp mạch → phải hiểu là thầy Nam.
    assert _resolve("thầy có bằng cấp gì", shown, hist).tutor_id == "a"
    assert _resolve("học phí bao nhiêu", shown, hist).tutor_id == "a"
    assert _resolve("đúng rồi", shown, hist).tutor_id == "a"


def test_khong_co_ngu_canh_thi_van_phai_hoi_lai():
    """Focus chỉ là phương án dự phòng — không có mạch hội thoại thì vẫn không được đoán."""
    shown = [ShownTutor(tutor_id="a", name="Nguyễn Hoàng Nam"),
             ShownTutor(tutor_id="b", name="Lê Tuấn Tú")]
    assert _resolve("thầy có bằng cấp gì", shown, []) is None
