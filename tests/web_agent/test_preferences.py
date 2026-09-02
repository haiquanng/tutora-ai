"""
Test tiêu chí MỀM tích luỹ ("con mất gốc", "cần cô kiên nhẫn", "ưu tiên thạc sĩ").

Trước đây `query` gửi cho ranking chỉ là TIN NHẮN HIỆN TẠI: mong muốn nêu ở lượt 1 biến
mất ngay từ lượt 2, xếp hạng như thể user chưa từng nói. Khác với filter cứng (môn/lớp/
giá/lịch) vốn đã tích luỹ, mấy tiêu chí này không có cột nào trong DB nên chỉ ảnh hưởng
THỨ TỰ — và vì thế reply chỉ được nói "ưu tiên", không được nói "đã lọc".
"""
from __future__ import annotations

import asyncio

from app.services.web_agent.agent import _merge_filters, _append_preference
from app.services.web_agent.handlers import tutor as tutor_mod
from app.services.web_agent.handlers.base import HandlerContext
from app.models.schemas import TutorChatContext, TutorChatFilters


def _ctx(message: str, filters: TutorChatFilters) -> HandlerContext:
    return HandlerContext(message=message, history=[], context=TutorChatContext(),
                          filters=filters, router_reply="", suggestions=[], shown_tutors=[])


def test_cong_don_qua_cac_luot():
    prev = TutorChatFilters(subject_id=1, preferences="con mất gốc")
    merged = _merge_filters(prev, {"preferences": "cần cô kiên nhẫn"})
    assert merged.preferences == "con mất gốc, cần cô kiên nhẫn"


def test_khong_lap_lai_mong_muon_da_co():
    assert _append_preference("con mất gốc", "con mất gốc") == "con mất gốc"
    assert _append_preference("con mất gốc, cần cô kiên nhẫn", "CẦN CÔ KIÊN NHẪN") \
        == "con mất gốc, cần cô kiên nhẫn"


def test_co_tran_do_dai():
    """Text này nhồi vào query embedding — để trôi vô hạn thì mong muốn cũ pha loãng cái mới."""
    assert len(_append_preference("x" * 290, "y" * 50)) == 300


def test_khong_nhac_thi_giu_nguyen():
    prev = TutorChatFilters(preferences="con mất gốc")
    assert _merge_filters(prev, {"max_rate": 200000}).preferences == "con mất gốc"


def test_clear_xoa_duoc():
    prev = TutorChatFilters(preferences="con mất gốc")
    assert _merge_filters(prev, {"preferences": "__clear__"}).preferences is None


def test_query_gom_ca_mong_muon_cu():
    ctx = _ctx("còn ai khác không", TutorChatFilters(subject_id=1))
    ctx.prior_preferences = "con mất gốc, cần cô kiên nhẫn"
    q = tutor_mod._rank_query(ctx)
    assert "con mất gốc" in q and "cần cô kiên nhẫn" in q and "còn ai khác không" in q


def test_query_khong_lap_phan_da_co_trong_tin_nhan():
    ctx = _ctx("mình cần cô kiên nhẫn", TutorChatFilters())
    ctx.prior_preferences = "cần cô kiên nhẫn"
    assert tutor_mod._rank_query(ctx) == "mình cần cô kiên nhẫn"


def _fake_fetch(tutors):
    async def _f(context, filters, query, *, extra_top_k=0):
        _f.query = query
        return {"tutors": tutors, "aiRanked": True}
    return _f


def test_handler_gui_mong_muon_cu_xuong_ranking(monkeypatch):
    async def _subjects(): return [{"subjectId": 1, "subjectName": "Toán Học"}]
    async def _grades(): return []
    monkeypatch.setattr(tutor_mod, "_get_subjects", _subjects)
    monkeypatch.setattr(tutor_mod, "_get_grades", _grades)
    monkeypatch.setattr(tutor_mod, "_follow_up_question", lambda ctx, reply: "")
    fake = _fake_fetch([{"tutorId": "a", "fullName": "X"}])
    monkeypatch.setattr(tutor_mod, "_fetch_candidates", fake)

    ctx = _ctx("còn ai khác không",
               TutorChatFilters(subject_id=1, preferences="con mất gốc"))
    ctx.prior_preferences = "con mất gốc"
    resp = asyncio.run(tutor_mod.TutorHandler().handle(ctx))

    assert "con mất gốc" in fake.query
    # Tiêu chí mềm chỉ đổi THỨ TỰ, không loại ai → chỉ được nói "ưu tiên".
    assert "Mình ưu tiên con mất gốc" in resp.reply
    assert "lọc" not in resp.reply


def test_khong_hua_uu_tien_khi_ranking_hong(monkeypatch):
    """ai_ranked=False nghĩa là .NET đã BỎ query — nói "ưu tiên" lúc đó là hứa hão."""
    async def _subjects(): return [{"subjectId": 1, "subjectName": "Toán Học"}]
    async def _grades(): return []
    monkeypatch.setattr(tutor_mod, "_get_subjects", _subjects)
    monkeypatch.setattr(tutor_mod, "_get_grades", _grades)

    async def _f(context, filters, query, *, extra_top_k=0):
        return {"tutors": [{"tutorId": "a", "fullName": "X"}], "aiRanked": False}
    monkeypatch.setattr(tutor_mod, "_fetch_candidates", _f)

    resp = asyncio.run(tutor_mod.TutorHandler().handle(
        _ctx("tìm gia sư", TutorChatFilters(subject_id=1, preferences="con mất gốc"))))

    assert "ưu tiên" not in resp.reply


# ─────────── Ổn định giữa các lần chạy + nhãn "PHÙ HỢP NHẤT" ───────────

def test_query_dung_nguyen_van_tin_nhan_khong_dung_ban_dien_giai():
    """LLM diễn giải lại lời user mỗi lần một kiểu ("ở UK hoặc Trung Quốc" /
    "ở nước ngoài (UK, Trung Quốc)"). Ghép bản diễn giải vào query khiến cùng một câu hỏi
    cho ra thứ hạng khác nhau giữa các lần chạy."""
    ctx = _ctx("tìm gia sư tốt nghiệp Nottingham, dạy ở UK hoặc trung quốc",
               TutorChatFilters(subject_id=1,
                                preferences="tốt nghiệp trường quốc tế (Nottingham), "
                                            "kinh nghiệm giảng dạy ở nước ngoài"))
    q = tutor_mod._rank_query(ctx)
    assert "Nottingham" in q and "UK" in q, "giữ nguyên chữ của user"
    assert "nước ngoài" not in q, "bản diễn giải của LLM không được lọt vào query"
    # Tất định: cùng input, cùng output — không phụ thuộc lần gọi LLM nào.
    assert tutor_mod._rank_query(ctx) == q


def test_bo_phan_da_thanh_filter_cung_khoi_query():
    """Môn/lớp/giá/lịch đã lọc ở SQL rồi; để lại trong query chỉ làm loãng tín hiệu phân
    biệt — đúng hiện tượng đã đo trong tutor_embed.py (nhắc lại môn/lớp làm ai cũng giống
    ai). Đo được: để nguyên câu thì người khớp nhất văng khỏi top 2."""
    q = tutor_mod._semantic_part(
        "tôi muốn tìm gia sư toán 12, rảnh được t7 và CN, tốt nghiệp Nottingham")
    assert "t7" not in q.lower() and "cn" not in q.lower()
    assert "Nottingham" in q

    # Câu CHỈ có tiêu chí cứng → cắt sạch sẽ thành rỗng, phải giữ nguyên bản gốc.
    goc = "tìm gia sư toán lớp 9 dưới 200k rảnh cuối tuần"
    assert tutor_mod._semantic_part(goc) == goc


def test_van_giu_mong_muon_cua_luot_truoc():
    ctx = _ctx("còn ai khác không", TutorChatFilters(subject_id=1))
    ctx.prior_preferences = "con mất gốc, cần cô kiên nhẫn"
    q = tutor_mod._rank_query(ctx)
    assert "con mất gốc" in q and "cần cô kiên nhẫn" in q


def test_nhan_phu_hop_nhat_chi_khi_thuc_su_vuot_troi():
    """Nhãn này là một KHẲNG ĐỊNH. Gắn lên người hơn nửa vời thì user mở hồ sơ ra sẽ thấy
    chẳng liên quan gì tới tiêu chí mình vừa nêu."""
    vuot_troi = [{"tutorId": "a", "aiSimilarity": 0.81}, {"tutorId": "b", "aiSimilarity": 0.71}]
    sat_nut = [{"tutorId": "a", "aiSimilarity": 0.735}, {"tutorId": "b", "aiSimilarity": 0.729}]

    assert tutor_mod._has_clear_best(vuot_troi, ai_ranked=True) is True
    assert tutor_mod._has_clear_best(sat_nut, ai_ranked=True) is False
    # Ranking hỏng → không có cơ sở nào để nói ai phù hợp nhất.
    assert tutor_mod._has_clear_best(vuot_troi, ai_ranked=False) is False
    # Chỉ có 1 người thì không phải so với ai.
    assert tutor_mod._has_clear_best([{"tutorId": "a", "aiSimilarity": 0.7}], True) is True
