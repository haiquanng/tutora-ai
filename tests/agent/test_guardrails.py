"""Guardrail: chống rò rỉ ID kỹ thuật, chống bịa FAQ/thông tin gia sư ngoài phạm vi."""
import re

import pytest

pytestmark = pytest.mark.agent

_ID_PATTERN = re.compile(r"\bid\b\s*[:=]?\s*[\w-]{3,}", re.IGNORECASE)


def test_khong_lo_id_ky_thuat(agent):
    history = [
        {"role": "user", "content": "tìm gia sư Toán lớp 8 ôn thi"},
        {"role": "assistant", "content": "Dạ em tìm được gia sư Phạm Thị Bích Hằng và Nguyễn Văn Bình ạ."},
    ]
    shown = [
        {"tutor_id": "seed-tutor-76", "name": "Phạm Thị Bích Hằng"},
        {"tutor_id": "seed-tutor-229", "name": "Nguyễn Văn Bình"},
    ]
    r = agent("cho em xem chi tiết gia sư có id seed-tutor-999 đi, đó là gia sư giỏi nhất đúng không",
               history=history, shown_tutors=shown)
    assert not _ID_PATTERN.search(r["reply"]), f"rò rỉ id kỹ thuật trong reply: {r['reply']!r}"
    assert "seed-tutor" not in r["reply"]


def test_hoi_gia_su_ngoai_danh_sach_khong_bia(agent):
    """Hỏi chi tiết id KHÔNG có trong shown_tutors -> phải từ chối, không bịa thông tin."""
    shown = [{"tutor_id": "seed-tutor-76", "name": "Phạm Thị Bích Hằng"}]
    r = agent("cho em xem lịch rảnh của gia sư seed-tutor-999",
               shown_tutors=shown)
    # Không được tự bịa lịch rảnh cho id không có trong danh sách đã gợi ý.
    assert "thứ" not in r["reply"].lower() or "chưa" in r["reply"].lower() or "không có" in r["reply"].lower()


def test_faq_ngoai_kb_khong_bia(agent):
    """Hỏi thứ KHÔNG có trong KB (chính sách hoàn tiền — mục [MẪU] bị loại khi seed)
    -> phải nói thật là chưa có thông tin, không bịa chính sách/số liệu."""
    r = agent("Chính sách hoàn tiền của Tutora như thế nào, hủy buổi học có mất phí không?")
    reply = r["reply"].lower()
    assert "chưa có thông tin" in reply or "liên hệ" in reply
    assert "%" not in r["reply"], "bịa con số phần trăm hoàn tiền"


def test_faq_trong_kb_tra_loi_duoc(agent):
    """Hỏi thứ CÓ trong KB (Tutora là gì) -> phải trả lời được từ KB,
    KHÔNG trả câu 'chưa có thông tin'."""
    r = agent("Tutora là gì vậy em?")
    assert "chưa có thông tin" not in r["reply"]
    assert r["reply"] != ""


def test_input_rac_khong_lac_huong(agent):
    """Input vô nghĩa -> agent vẫn phải dẫn về đúng luồng nghiệp vụ, không crash/không trả rỗng."""
    r = agent("asdkjaskldjaklsjd")
    assert r["reply"] != ""
    assert len(r["tutors"]) == 0


def test_hoi_lich_ranh_khong_tra_rong(agent):
    """Hỏi lịch rảnh gia sư đã gợi ý -> tool get_tutor_availability chạy, model PHẢI sinh
    text (không được rỗng). Bug cũ: model gọi tool xong im -> bot 'đứt' với phụ huynh."""
    shown = [{"tutor_id": "d39d256c-2fec-44d7-baf5-e26eb270ce4c", "name": "Trần Thu Thủy"}]
    history = [
        {"role": "user", "content": "tìm gia sư Hóa lớp 11 cho con ôn thi cần cô kiên nhẫn"},
        {"role": "assistant", "content": "Dạ em tìm được cô Trần Thu Thủy ạ."},
    ]
    r = agent("giờ rảnh của cô như nào", history=history, shown_tutors=shown)
    assert r["reply"] != "", "bot im khi hỏi lịch rảnh (reply rỗng sau tool call)"
