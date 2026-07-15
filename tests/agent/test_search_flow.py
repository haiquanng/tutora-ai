"""Kịch bản search — thiết kế "LUÔN mở Mini App form" (quyết định sản phẩm 2026-07-15).

Mọi lượt PH thể hiện ý định tìm gia sư mà CHƯA từng được gợi ý gia sư nào trong phiên
(shown_tutors rỗng) đều mở lại Mini App form (reopen_mini_app=True), bất kể context đã
có sẵn môn/lớp/mục tiêu/mong muốn hay chưa — không còn hỏi-gộp/slot-filling qua chat nữa.
CHỈ ngoại lệ: message là đúng trigger cố định từ chính Mini App form submit
(MiniAppSearchFlow.handleFormSubmit bên tutora-zalo-bot) — lúc đó search THẲNG, không
mở lại form (tránh vòng lặp vô hạn submit -> form -> submit...)."""
import pytest

pytestmark = pytest.mark.agent


def test_fresh_intent_luon_mo_form_du_da_co_du_slot(agent):
    """Câu đầu tiên dù đã nói đủ môn/lớp/mục tiêu -> vẫn mở Mini App form, KHÔNG search
    thẳng qua chat (thay thế hành vi "hỏi lượt gộp" cũ)."""
    r = agent("tìm gia sư Toán lớp 8 ôn thi cho con em")
    assert len(r["tutors"]) == 0, "phải mở Mini App form, không search thẳng từ chat"
    assert r["reply"] != ""
    assert r.get("reopen_mini_app") is True


def test_thieu_thong_tin_hoi_lai(agent):
    """Chỉ có môn, thiếu mọi thứ khác -> mở Mini App form, KHÔNG search."""
    r = agent("em cần tìm gia sư dạy Tiếng Anh")
    assert len(r["tutors"]) == 0
    assert r["reply"] != ""
    assert r.get("reopen_mini_app") is True


def test_tiep_tuc_hoi_thoai_van_mo_form_chua_search(agent):
    """PH trả lời tiếp câu hỏi cũ (trước khi có thiết kế mới) giữa chat, CHƯA từng được
    gợi ý gia sư nào -> vẫn mở lại Mini App form, KHÔNG tự search thẳng qua chat."""
    history = [
        {"role": "user", "content": "tìm gia sư Toán lớp 8 ôn thi cho con em"},
        {"role": "assistant", "content": "Dạ để em gửi form nhanh để anh/chị điền thông tin cho tiện nhé ạ!"},
    ]
    r = agent("anh/chị chưa điền form được, em tìm gia sư giúp qua đây luôn nhé", history=history,
              context={"subject_id": 1, "grade_level_id": 56, "goal": "ôn thi"})
    assert len(r["tutors"]) == 0, "chưa search lần nào mà lại tự search thẳng qua chat"
    assert r.get("reopen_mini_app") is True


def test_asked_preferences_cu_khong_con_tac_dung_neu_chua_search(agent):
    """asked_preferences=True còn sót lại từ thiết kế cũ, nhưng CHƯA search lần nào
    (shown_tutors rỗng) -> vẫn mở Mini App form theo thiết kế mới, không search thẳng."""
    r = agent("bé cần củng cố kiến thức",
              history=[{"role": "user", "content": "tìm gia sư Toán lớp 8"},
                       {"role": "assistant", "content": "Dạ bé học với mục tiêu gì ạ?"}],
              context={"subject_id": 1, "grade_level_id": 56, "asked_preferences": True})
    assert len(r["tutors"]) == 0
    assert r.get("reopen_mini_app") is True


def test_mini_app_submit_trigger_search_thang_khong_mo_lai_form(agent):
    """Trigger message cố định từ Mini App form submit -> search THẲNG, KHÔNG mở lại
    form (đây là ngoại lệ DUY NHẤT của "luôn mở form" — tránh vòng lặp vô hạn)."""
    r = agent("Tìm gia sư giúp tôi ạ",
              context={"subject_id": 1, "grade_level_id": 56, "goal": "ôn thi",
                       "asked_preferences": True})
    assert r.get("reopen_mini_app") is not True, "submit từ Mini App mà vẫn mở lại form"
    assert len(r["tutors"]) > 0, "submit từ Mini App phải search thẳng, không hỏi gì thêm"
    assert all("Toán" in "".join(t.get("subjects") or []) for t in r["tutors"])


def test_mon_khong_ton_tai_khong_bia_gia_su(agent):
    """Môn không có trong hệ thống -> KHÔNG bịa gia sư môn khác (kể cả khi nhu cầu đủ)."""
    history = [
        {"role": "assistant", "content": "Dạ mục tiêu và mong muốn của anh/chị thế nào ạ?"},
    ]
    r = agent("bé lớp 8 muốn học Thiên văn học nâng cao, cần thầy giỏi kiên nhẫn dạy online",
              history=history)
    assert len(r["tutors"]) == 0, "bịa gia sư cho môn không tồn tại"


def test_doi_mon_khong_lan_gia_su_cu(agent):
    """Đổi môn giữa chat, đã có đủ nhu cầu -> tutors phải đúng môn MỚI, không lẫn môn cũ."""
    history = [
        {"role": "user", "content": "tìm gia sư Toán lớp 8 cho bé mất gốc cần cô kiên nhẫn dạy online"},
        {"role": "assistant", "content": "Dạ em tìm được vài gia sư Toán phù hợp cho bé nhé!"},
        {"role": "user", "content": "à thôi đổi qua tìm gia sư Sinh học cho bé đi em"},
        {"role": "assistant", "content": "Dạ anh/chị muốn đổi sang môn Sinh học cho bé lớp 8, cùng nhu cầu như vậy đúng không ạ?"},
    ]
    r = agent("ừ đúng rồi, vẫn bé đó mất gốc cần cô kiên nhẫn dạy online",
              history=history, context={"subject_id": 1, "grade_level_id": 56})
    # Model có thể confirm đổi môn thêm 1 lượt (tutors=0) HOẶC search luôn. Điều BẮT BUỘC:
    # nếu đã search thì KHÔNG được lẫn gia sư Toán (môn cũ) vào kết quả Sinh (môn mới).
    all_subjects = {s for t in r["tutors"] for s in (t.get("subjects") or [])}
    assert "Toán Học" not in all_subjects, "lẫn gia sư môn cũ (Toán) sang kết quả môn mới (Sinh)"
