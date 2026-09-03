"""
Test XOÁ filter — user bảo bỏ một tiêu chí thì nó phải biến mất.

Đổi giá trị thì LLM làm tốt; BỎ thì phải nhận ra ý định, LLM trượt là filter cũ bám lại
và âm thầm lọc sai. Bug thật 2026-09-02: "giờ không cần xét về lịch rảnh nữa" mà
available_days vẫn [6,7] → gia sư phù hợp bị loại oan, còn bot thì vẫn nói "rảnh T7 và CN".

_fix_clears là hàm thuần → test tất định, không phụ thuộc LLM.
"""
from __future__ import annotations

import pytest

from app.services.web_agent.router import _fix_clears
from app.services.web_agent.agent import _merge_filters
from app.models.schemas import TutorChatFilters

CLEAR = "__clear__"


def _run(message: str, filters: dict | None = None) -> dict:
    return _fix_clears(dict(filters or {}), message)


@pytest.mark.parametrize("msg", [
    "giờ không cần xét về lịch rảnh nữa, tôi muốn tìm gia sư nhiều kinh nghiệm",
    "bỏ qua lịch học đi",
    "lịch nào cũng được",
    "khung giờ nào cũng được ạ",
    "khong can lich nua",          # gõ không dấu
])
def test_xoa_ca_cum_lich(msg):
    """Ba field lịch phải mất CÙNG NHAU. Xoá ngày mà giữ khung giờ thì vẫn còn lọc theo
    giờ — user tưởng đã bỏ hết ràng buộc nhưng thực tế vẫn bị chặn."""
    got = _run(msg)
    for field in ("available_days", "available_days_match", "available_from", "available_to"):
        assert got.get(field) == CLEAR, f"thiếu clear cho {field}"


def test_xoa_gia_va_gioi_tinh():
    assert _run("bỏ giới hạn giá đi").get("max_rate") == CLEAR
    assert _run("học phí nào cũng được").get("min_rate") == CLEAR
    assert _run("không cần cô giáo nữa, thầy giáo cũng được").get("tutor_gender") == CLEAR
    assert _run("giới tính nào cũng được").get("tutor_gender") == CLEAR


def test_chi_xoa_truc_duoc_nhac_trong_cung_menh_de():
    """"bỏ giới hạn giá, tìm cô giáo" — quét cả câu thì cụm "bỏ" ở vế đầu sẽ xoá luôn
    giới tính ở vế sau."""
    got = _run("bỏ giới hạn giá, mình muốn tìm cô giáo")
    assert got.get("max_rate") == CLEAR
    assert "tutor_gender" not in got


@pytest.mark.parametrize("msg", [
    "tìm gia sư có 5 năm kinh nghiệm",        # "năm" bỏ dấu = "nam" — không được hiểu là giới tính
    "cô ấy dạy thế nào",
    "mình cần gia sư dạy tốt, không cần bằng thạc sĩ",   # trục không hỗ trợ → không xoá bừa
    "tìm gia sư toán lớp 9 rảnh cuối tuần",
])
def test_khong_xoa_nham(msg):
    assert _run(msg) == {}


def test_clear_thang_gia_tri_vua_trich():
    """Câu vừa nêu ngày vừa bảo bỏ lịch → phải XOÁ, không giữ ngày."""
    got = _run("thôi không cần lịch T7 nữa", {"available_days": [6]})
    assert got["available_days"] == CLEAR


def test_merge_bien_clear_thanh_none():
    """Kiểm tra nốt vế sau: "__clear__" phải thành None khi merge, khác hẳn null (giữ nguyên)."""
    prev = TutorChatFilters(subject_id=1, available_days=[6, 7],
                            available_days_match="all", available_from="08:00",
                            available_to="21:00", max_rate=200000)
    merged = _merge_filters(prev, _run("không cần xét lịch rảnh nữa"))

    assert merged.available_days is None
    assert merged.available_days_match is None
    assert merged.available_from is None and merged.available_to is None
    # Trục không được nhắc phải GIỮ NGUYÊN — im lặng không có nghĩa là bỏ.
    assert merged.subject_id == 1 and merged.max_rate == 200000
