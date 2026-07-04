"""Kịch bản search_tutors: đủ thông tin, thiếu thông tin, môn không tồn tại."""
import pytest

pytestmark = pytest.mark.agent


def test_du_thong_tin_search_ngay(agent):
    """Đủ môn+lớp+mục tiêu ngay lượt đầu -> phải search luôn, KHÔNG hỏi lại."""
    r = agent("tìm gia sư Toán lớp 8 ôn thi cho con em")
    assert len(r["tutors"]) > 0
    assert r["reply"] != "", "reply rỗng dù đã search thành công"
    assert all("Toán" in "".join(t.get("subjects") or []) for t in r["tutors"])


def test_thieu_thong_tin_hoi_lai(agent):
    """Chỉ có môn, thiếu lớp+mục tiêu -> phải hỏi thêm, KHÔNG search."""
    r = agent("em cần tìm gia sư dạy Tiếng Anh")
    assert len(r["tutors"]) == 0
    assert r["reply"] != ""


def test_multi_turn_tich_luy_thong_tin(agent):
    """Turn 2 bổ sung đủ thông tin -> phải search, không hỏi lại từ đầu."""
    history = [
        {"role": "user", "content": "em cần tìm gia sư dạy Tiếng Anh"},
        {"role": "assistant", "content": "Dạ, bé nhà mình học lớp mấy và mục tiêu học là gì ạ?"},
    ]
    r = agent("lớp 6, mất gốc luôn", history=history)
    assert len(r["tutors"]) > 0
    assert r["reply"] != ""


def test_mon_khong_ton_tai_khong_bia_gia_su(agent):
    """Môn không có trong hệ thống -> KHÔNG được bịa gia sư môn khác để lấp chỗ trống."""
    r = agent("tìm gia sư dạy môn Thiên văn học lớp 8 nâng cao")
    assert len(r["tutors"]) == 0, "bịa gia sư cho môn không tồn tại trong hệ thống"


def test_doi_mon_khong_lan_gia_su_cu(agent):
    """Đổi môn giữa chat, sau khi đồng ý -> tutors trả về phải đúng môn MỚI, không lẫn môn cũ."""
    history = [
        {"role": "user", "content": "tìm gia sư Toán lớp 8 ôn thi"},
        {"role": "assistant", "content": "Dạ em tìm được vài gia sư Toán lớp 8 phù hợp cho bé nhé!"},
        {"role": "user", "content": "à thôi đổi qua tìm gia sư Sinh học cho bé đi em"},
        {"role": "assistant", "content": "Dạ anh/chị muốn đổi sang tìm gia sư môn Sinh học cho bé lớp 8, đúng không ạ?"},
    ]
    r = agent("ừ đúng rồi", history=history, context={"subject_id": 1, "grade_level_id": 56})
    assert len(r["tutors"]) > 0
    all_subjects = {s for t in r["tutors"] for s in (t.get("subjects") or [])}
    assert "Toán Học" not in all_subjects, "lẫn gia sư môn cũ (Toán) sang kết quả môn mới (Sinh)"
