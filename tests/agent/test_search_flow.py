"""Kịch bản search_tutors theo triết lý TƯ VẤN TRƯỚC, RECOMMEND SAU.

Agent KHÔNG được bắn card ngay khi mới có môn+lớp — phải hỏi đủ nhu cầu (mục tiêu +
tình trạng bé + mong muốn gia sư) rồi mới recommend. Gate cứng trong _search_tutors
enforce điều này (chặn nếu 'query' nhu cầu chưa đủ dài)."""
import pytest

pytestmark = pytest.mark.agent


def test_moi_co_mon_lop_thi_hoi_them_khong_ban_card(agent):
    """Môn + lớp + mục tiêu ngắn gọn -> CHƯA đủ để tư vấn -> phải hỏi thêm, KHÔNG recommend."""
    r = agent("tìm gia sư Toán lớp 8 ôn thi cho con em")
    assert len(r["tutors"]) == 0, "bắn card quá sớm khi chưa hiểu đủ nhu cầu bé"
    assert r["reply"] != ""


def test_thieu_thong_tin_hoi_lai(agent):
    """Chỉ có môn, thiếu mọi thứ khác -> phải hỏi thêm, KHÔNG search."""
    r = agent("em cần tìm gia sư dạy Tiếng Anh")
    assert len(r["tutors"]) == 0
    assert r["reply"] != ""


def test_du_nhu_cau_thi_recommend(agent):
    """Hội thoại đã thu thập đủ mục tiêu + tình trạng + mong muốn -> ĐƯỢC recommend."""
    history = [
        {"role": "user", "content": "tìm gia sư Toán cho bé lớp 8"},
        {"role": "assistant", "content": "Dạ mục tiêu học của bé là gì ạ?"},
        {"role": "user", "content": "bé mất gốc từ lớp 7, tiếp thu chậm"},
        {"role": "assistant", "content": "Dạ anh/chị mong muốn gia sư thế nào ạ?"},
    ]
    r = agent("cần cô kiên nhẫn dạy chậm dễ hiểu, học online",
              history=history, context={"subject_id": 1, "grade_level_id": 56})
    assert len(r["tutors"]) > 0, "đã đủ nhu cầu mà vẫn không recommend"
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
