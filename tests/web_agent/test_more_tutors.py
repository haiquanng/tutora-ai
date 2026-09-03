"""
Test "còn gia sư nào khác không" — cùng tiêu chí, khác NGƯỜI.

Trước đây handler web không loại gia sư đã giới thiệu (Zalo có exclude_tutor_ids, web
không), nên câu này trả về ĐÚNG 2 người vừa hiện. Lỗi bị che vì bug rơi filter làm pool
đổi hẳn mỗi lượt — vá filter xong là lộ ra ngay.

_wants_more / _exclude_ids là hàm thuần → test tất định, không cần LLM.
"""
from __future__ import annotations

import asyncio

import pytest

from app.services.web_agent.handlers import tutor as tutor_mod
from app.services.web_agent.handlers.base import HandlerContext
from app.models.schemas import ShownTutor, TutorChatContext, TutorChatFilters


SHOWN = [ShownTutor(tutor_id="a", name="Lê Quốc Khánh"),
         ShownTutor(tutor_id="b", name="Lê Gia Nam")]


def _ctx(message: str, filters: TutorChatFilters | None = None, shown=None) -> HandlerContext:
    return HandlerContext(
        message=message, history=[], context=TutorChatContext(),
        filters=filters or TutorChatFilters(subject_id=1, grade_level_id=60),
        router_reply="", suggestions=[], shown_tutors=SHOWN if shown is None else shown,
    )


@pytest.mark.parametrize("msg", [
    "còn gia sư nào khác không",
    "còn ai khác không",
    "cho mình xem thêm người nữa",
    "gia sư khác đi",
    "con ai nua khong",          # gõ không dấu
    "mình không thích 2 người này",
])
def test_nhan_dien_xin_nguoi_khac(msg):
    assert tutor_mod._wants_more(msg) is True


@pytest.mark.parametrize("msg", [
    "tìm gia sư toán lớp 12",
    "dưới 200k thôi",
    "cô đó dạy bao lâu rồi",
    "mình muốn học thứ 7",
])
def test_khong_nham_cau_tim_kiem_binh_thuong(msg):
    assert tutor_mod._wants_more(msg) is False


def test_exclude_cong_don_qua_cac_luot():
    """Hỏi "còn ai khác" lần 2 phải loại CẢ người của lần 1 — shown_tutors chỉ giữ card của
    đúng lượt trước, không cộng dồn thì người cũ quay lại."""
    filters = TutorChatFilters(subject_id=1, exclude_tutor_ids=["x", "y"])
    got = tutor_mod._exclude_ids(_ctx("còn ai khác không", filters), wants_more=True)
    assert got == ["x", "y", "a", "b"]


def test_cau_tim_kiem_binh_thuong_xoa_exclude():
    """Đổi tiêu chí → làm mới pool. Giữ exclude cũ sẽ âm thầm loại mất gia sư tốt nhất."""
    filters = TutorChatFilters(subject_id=1, exclude_tutor_ids=["x", "y"])
    assert tutor_mod._exclude_ids(_ctx("tìm gia sư lý lớp 10", filters), wants_more=False) == []


def _fake_fetch(tutors):
    async def _f(context, filters, query, *, extra_top_k=0):
        _f.extra_top_k = extra_top_k
        return {"tutors": tutors, "aiRanked": True}
    return _f


def test_handler_loai_gia_su_da_gioi_thieu(monkeypatch):
    pool = [{"tutorId": "a", "fullName": "Lê Quốc Khánh"},
            {"tutorId": "b", "fullName": "Lê Gia Nam"},
            {"tutorId": "c", "fullName": "Lê Tuấn Tú"},
            {"tutorId": "d", "fullName": "Nguyễn Thị Hương"}]
    fake = _fake_fetch(pool)
    monkeypatch.setattr(tutor_mod, "_fetch_candidates", fake)

    ctx = _ctx("còn gia sư nào khác không")
    resp = asyncio.run(tutor_mod.TutorHandler().handle(ctx))

    ids = [c.tutor_id for c in resp.cards]
    assert ids == ["c", "d"], "phải là người MỚI, không bắn lại a/b vừa gợi ý"
    # Nới topK để bù phần bị loại, nếu không hỏi vài lần là cạn dù DB còn người khớp.
    assert fake.extra_top_k == 2
    # State ghi lại để lượt sau còn biết đã giới thiệu ai.
    assert resp.filters.exclude_tutor_ids == ["a", "b"]


def test_het_nguoi_moi_noi_ro_khac_voi_khong_tim_thay(monkeypatch):
    """"Đã giới thiệu hết" ≠ "không có ai khớp" — nói nhầm vế sau là phủ nhận luôn mấy
    gia sư vừa hiện ngay phía trên."""
    pool = [{"tutorId": "a", "fullName": "Lê Quốc Khánh"},
            {"tutorId": "b", "fullName": "Lê Gia Nam"}]
    monkeypatch.setattr(tutor_mod, "_fetch_candidates", _fake_fetch(pool))

    resp = asyncio.run(tutor_mod.TutorHandler().handle(_ctx("còn ai khác không")))

    assert resp.cards == []
    assert "đã giới thiệu hết" in resp.reply
    assert "chưa có gia sư nào khớp" not in resp.reply


def test_tim_kiem_binh_thuong_khong_loai_ai(monkeypatch):
    pool = [{"tutorId": "a", "fullName": "Lê Quốc Khánh"},
            {"tutorId": "b", "fullName": "Lê Gia Nam"}]
    fake = _fake_fetch(pool)
    monkeypatch.setattr(tutor_mod, "_fetch_candidates", fake)

    ctx = _ctx("tìm gia sư toán lớp 12",
               TutorChatFilters(subject_id=1, exclude_tutor_ids=["a"]))
    resp = asyncio.run(tutor_mod.TutorHandler().handle(ctx))

    assert [c.tutor_id for c in resp.cards] == ["a", "b"]
    assert fake.extra_top_k == 0
    assert resp.filters.exclude_tutor_ids is None


# ─────────── Câu trả lời phải sinh từ FILTER ĐÃ ÁP DỤNG, không từ lời user ───────────

def _patch_danh_muc(monkeypatch):
    async def _subjects(): return [{"subjectId": 1, "subjectName": "Toán Học"}]
    async def _grades(): return [{"gradeLevelId": 60, "gradeName": "Lớp 12"}]
    monkeypatch.setattr(tutor_mod, "_get_subjects", _subjects)
    monkeypatch.setattr(tutor_mod, "_get_grades", _grades)


def test_cau_tra_loi_neu_dung_dieu_kien_da_loc(monkeypatch):
    """Bug 2026-09-02: SQL lọc "T7 HOẶC CN" nhưng bot nói "rảnh cả T7 và Chủ Nhật"."""
    _patch_danh_muc(monkeypatch)
    monkeypatch.setattr(tutor_mod, "_fetch_candidates",
                        _fake_fetch([{"tutorId": "a", "fullName": "Lê Gia Nam"}]))
    monkeypatch.setattr(tutor_mod, "_follow_up_question", lambda ctx, reply: "")

    f = TutorChatFilters(subject_id=1, grade_level_id=60,
                         available_days=[6, 7], available_days_match="any")
    resp = asyncio.run(tutor_mod.TutorHandler().handle(_ctx("tìm gia sư toán", f)))

    assert "Thứ Bảy hoặc Chủ Nhật" in resp.reply
    assert "và Chủ Nhật" not in resp.reply, "lọc 'hoặc' mà nói 'và' là khẳng định sai"
    assert "môn Toán Học" in resp.reply and "lớp 12" in resp.reply


def test_match_all_thi_moi_duoc_noi_ca_hai_ngay(monkeypatch):
    _patch_danh_muc(monkeypatch)
    monkeypatch.setattr(tutor_mod, "_fetch_candidates",
                        _fake_fetch([{"tutorId": "a", "fullName": "X"}]))
    monkeypatch.setattr(tutor_mod, "_follow_up_question", lambda ctx, reply: "")

    f = TutorChatFilters(subject_id=1, available_days=[6, 7], available_days_match="all")
    resp = asyncio.run(tutor_mod.TutorHandler().handle(_ctx("tìm gia sư toán", f)))

    assert "rảnh Thứ Bảy và Chủ Nhật" in resp.reply


def test_khong_ket_qua_thi_noi_ro_dang_loc_gi(monkeypatch):
    """Câu chung chung 'thử nới bớt yêu cầu' bắt user tự đoán tiêu chí nào đang chặn."""
    _patch_danh_muc(monkeypatch)
    monkeypatch.setattr(tutor_mod, "_fetch_candidates", _fake_fetch([]))

    f = TutorChatFilters(subject_id=1, grade_level_id=60, max_rate=200000,
                         available_days=[7], tutor_gender="female")
    resp = asyncio.run(tutor_mod.TutorHandler().handle(_ctx("tìm gia sư toán", f)))

    assert resp.cards == []
    for phai_co in ("môn Toán Học", "lớp 12", "dưới 200.000đ/giờ", "gia sư nữ", "Chủ Nhật"):
        assert phai_co in resp.reply, f"thiếu '{phai_co}' trong: {resp.reply}"


def test_khong_bia_tieu_chi_user_khong_neu(monkeypatch):
    """Câu cứng cũ luôn nói 'khớp môn/lớp và mức giá bạn cần' kể cả khi user không nêu giá."""
    _patch_danh_muc(monkeypatch)
    monkeypatch.setattr(tutor_mod, "_fetch_candidates",
                        _fake_fetch([{"tutorId": "a", "fullName": "X"}]))
    monkeypatch.setattr(tutor_mod, "_follow_up_question", lambda ctx, reply: "")

    resp = asyncio.run(tutor_mod.TutorHandler().handle(
        _ctx("tìm gia sư toán", TutorChatFilters(subject_id=1))))

    assert "học phí" not in resp.reply
    assert "lớp" not in resp.reply
    assert "gia sư nữ" not in resp.reply and "gia sư nam" not in resp.reply
