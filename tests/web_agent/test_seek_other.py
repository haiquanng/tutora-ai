"""
Test: "2 người đó không có chứng chỉ, tôi muốn tìm NGƯỜI KHÁC" phải đi TÌM, không phải
kể hồ sơ của chính người vừa bị chê.

Bug thật 2026-09-02 (2 lỗi chồng nhau):
  1. Router xếp câu đó vào tutor_info vì nó vẫn nhắc tới nhóm gia sư vừa hiện.
  2. _focus_from_history quét cả câu PHÂN ĐỊNH của chính bot ("Bạn muốn hỏi về A hay B ạ?"),
     thấy A trước nên chốt A — bot tự trả lời câu hỏi mà chính nó vừa đặt cho user.
Kết quả: user xin người khác, bot kể chứng chỉ của đúng người user vừa nói là không muốn.
"""
from __future__ import annotations

import pytest

from app.services.web_agent.agent import _seeks_other_tutor
from app.services.web_agent.handlers.tutor_info import _resolve, _focus_from_history
from app.models.schemas import ShownTutor

SHOWN = [ShownTutor(tutor_id="a", name="Hoàng Minh Hiếu"),
         ShownTutor(tutor_id="b", name="Trịnh Tuấn Anh")]

# Đúng câu phân định mà bot đã sinh ra trong hội thoại thật.
BOT_HOI_LAI = {"role": "assistant",
               "content": "Bạn muốn hỏi về Hoàng Minh Hiếu hay Trịnh Tuấn Anh ạ? "
                          "Cho mình biết tên để mình xem giúp nhé."}


@pytest.mark.parametrize("msg", [
    "không, 2 người đó tôi không thấy chứng chỉ, tôi muốn tìm người khác có chứng chỉ",
    "có ai có chứng chỉ hay bằng cấp gì không",
    "tìm gia sư khác đi",
    "đổi người khác giúp mình",
    "gia sư nào có bằng thạc sĩ không",
])
def test_nhan_dien_xin_nguoi_khac(msg):
    assert _seeks_other_tutor(msg) is True


@pytest.mark.parametrize("msg", [
    "cô ấy có chứng chỉ không",       # "cô ấy" bỏ dấu = "co ay", KHÔNG được lẫn với "có ai"
    "thầy Hiếu dạy bao lâu rồi",
    "người đầu tiên học phí bao nhiêu",
    "bạn ấy có kinh nghiệm luyện thi chưa",
])
def test_khong_nham_cau_hoi_ve_mot_nguoi(msg):
    assert _seeks_other_tutor(msg) is False


def test_cau_phan_dinh_cua_bot_khong_phai_focus():
    """Câu nhắc ≥2 gia sư tự nó đã mơ hồ — không thể dùng để chốt đang nói về ai."""
    assert _focus_from_history([BOT_HOI_LAI], SHOWN) is None


def test_van_giu_focus_khi_chi_nhac_mot_nguoi():
    """Không được siết quá tay: hỏi tiếp về cùng một người thì vẫn phải hiểu."""
    history = [{"role": "user", "content": "thầy Hiếu dạy sao"},
               {"role": "assistant", "content": "Thầy Hoàng Minh Hiếu có 3 năm kinh nghiệm ạ."}]
    assert _focus_from_history(history, SHOWN).tutor_id == "a"


def test_resolve_khong_doan_bua_sau_cau_phan_dinh():
    """Toàn cảnh: sau câu phân định của bot, user nói "2 người đó..." mà không nêu tên ai."""
    got = _resolve("không, 2 người đó tôi không thấy chứng chỉ, tôi muốn tìm người khác",
                   SHOWN, [BOT_HOI_LAI])
    assert got is None, "không nêu tên ai thì phải chịu là không rõ, không được chốt đại"


def test_so_dau_cau_khong_bi_hieu_nham_thanh_lop():
    """"2 người đó tôi không thấy chứng chỉ" từng bị hiểu thành "lớp 2" — nhánh số-đứng-đầu
    chỉ dành cho câu trả lời CỤT khi bot vừa hỏi "bé học lớp mấy?"."""
    from app.services.web_agent.router import _fix_grade

    grades = [{"gradeLevelId": 50, "gradeName": "Lớp 2"},
              {"gradeLevelId": 51, "gradeName": "Lớp 3"},
              {"gradeLevelId": 59, "gradeName": "Lớp 11"}]

    dai = _fix_grade({}, "2 người đó tôi không thấy chứng chỉ, muốn tìm người khác", grades)
    assert "grade_level_id" not in dai

    # Trả lời cụt thì vẫn phải hiểu.
    assert _fix_grade({}, "11", grades)["grade_level_id"] == 59
    assert _fix_grade({}, "11 nhé", grades)["grade_level_id"] == 59
    # Nêu rõ "lớp" thì dài mấy cũng nhận.
    assert _fix_grade({}, "mình cần tìm gia sư cho bé lớp 3 nhé bạn", grades)["grade_level_id"] == 51
