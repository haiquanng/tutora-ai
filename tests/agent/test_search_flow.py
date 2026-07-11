"""Kịch bản search theo gate "HỎI 1 LẦN, MỀM" (agents/agentscenarios.md KB-A bước 4).

Bắt buộc cứng: subject + grade + goal. Sau đó agent hỏi GỘP đúng 1 lượt tuỳ chọn
(hình thức học + mong muốn về gia sư) rồi search — PH không có yêu cầu ("sao cũng
được") thì vẫn search, KHÔNG hỏi lại lần 2. PH giục → search ngay, bỏ câu hỏi mềm."""
import pytest

pytestmark = pytest.mark.agent


def test_moi_co_mon_lop_thi_hoi_them_khong_ban_card(agent):
    """Đủ 3 slot cứng nhưng CHƯA hỏi lượt gộp tuỳ chọn -> hỏi 1 câu (mong muốn thêm),
    CHƯA recommend lượt này. Lượt sau mới search dù PH có trả lời hay không."""
    r = agent("tìm gia sư Toán lớp 8 ôn thi cho con em")
    assert len(r["tutors"]) == 0, "phải hỏi lượt gộp tuỳ chọn 1 lần trước khi bắn card"
    assert r["reply"] != ""
    # Agent phải đánh dấu đã hỏi (NestJS persist) để lượt sau không hỏi lại.
    assert (r.get("context_patch") or {}).get("asked_preferences") is True


def test_thieu_thong_tin_hoi_lai(agent):
    """Chỉ có môn, thiếu mọi thứ khác -> phải hỏi thêm, KHÔNG search."""
    r = agent("em cần tìm gia sư dạy Tiếng Anh")
    assert len(r["tutors"]) == 0
    assert r["reply"] != ""


def test_tu_choi_luot_gop_van_recommend(agent):
    """PH trả lời 'sao cũng được' cho lượt gộp tuỳ chọn -> VẪN search, không hỏi lại lần 2."""
    history = [
        {"role": "user", "content": "tìm gia sư Toán lớp 8 ôn thi cho con em"},
        {"role": "assistant", "content": "Dạ anh/chị muốn bé học online hay gia sư đến nhà ạ? "
         "Có mong muốn gì thêm về gia sư không? Không có thì em tìm luôn ạ!"},
    ]
    r = agent("sao cũng được em ơi", history=history,
              context={"subject_id": 1, "grade_level_id": 56, "goal": "ôn thi"})
    assert len(r["tutors"]) > 0, "PH đã từ chối lượt gộp mà vẫn không search (hỏi lặp?)"


def test_da_hoi_luot_gop_khong_hoi_lai(agent):
    """asked_preferences=True (NestJS persist từ lượt trước) -> đủ 3 slot là search luôn."""
    r = agent("bé cần củng cố kiến thức",
              history=[{"role": "user", "content": "tìm gia sư Toán lớp 8"},
                       {"role": "assistant", "content": "Dạ bé học với mục tiêu gì ạ?"}],
              context={"subject_id": 1, "grade_level_id": 56, "asked_preferences": True})
    assert len(r["tutors"]) > 0, "đã hỏi lượt gộp rồi mà vẫn chưa search"


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
