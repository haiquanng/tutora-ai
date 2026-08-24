"""
Test filter LỊCH RẢNH (tutor_availability) — nhóm "filter cứng", phải đúng ~100%.
"""
from __future__ import annotations

import asyncio

import pytest

from app.core.dependencies import get_supabase
from app.services.web_agent import router as router_mod
from app.services.tutoring_shared.candidates import _get_subjects, _get_grades
from app.models.schemas import TutorChatFilters

SUBJECT_VAT_LY = 3
GRADE_LOP_9 = 57
SAT_EVENING = ([6], "18:00", "21:00")   # tối Thứ 7
SAT_EVENING_UTC = ("11:00", "14:00")


def _tutors_free(day: int, start: str, end: str) -> set[str]:
    """Tập gia sư THẬT SỰ rảnh khung đó (nguồn sự thật để đối chiếu)."""
    sb = get_supabase()
    prices = (sb.table("tutor_subject_grade_prices")
              .select("tutor_id")
              .eq("subject_id", SUBJECT_VAT_LY).eq("grade_level_id", GRADE_LOP_9)
              .eq("is_active", True).execute().data or [])
    pool = {p["tutor_id"] for p in prices}
    if not pool:
        return set()

    avail = (sb.table("tutor_availability")
             .select("tutor_id, day_of_week_id, start_time, end_time")
             .in_("tutor_id", list(pool)).eq("day_of_week_id", day)
             .execute().data or [])
    # DB trả "HH:MM:SS", tham số là "HH:MM" → so chuỗi thẳng sẽ sai ("18:00:00" > "18:00").
    def _hm(v: str | None, fallback: str) -> str:
        return (v or fallback)[:5]

    # Cùng MỘT khoảng rảnh phải phủ trọn khung giờ (không ghép 2 khoảng rời).
    ok = {a["tutor_id"] for a in avail
          if _hm(a.get("start_time"), "99:99") <= start and _hm(a.get("end_time"), "00:00") >= end}

    profs = (sb.table("tutor_profiles").select("tutor_id, profile_status, is_public")
             .in_("tutor_id", list(ok)).execute().data or []) if ok else []
    return {p["tutor_id"] for p in profs
            if p.get("profile_status") == "active" and p.get("is_public")}


def test_router_trich_dung_lich_toi_thu_7():
    """"tối thứ 7" phải ra available_days=[6] + khung 18:00-21:00 (không để rơi vào query)."""
    subjects, grades = asyncio.run(_get_subjects()), asyncio.run(_get_grades())
    routed = asyncio.run(router_mod.route(
        [], "Có gia sư Vật lý lớp 9 nào rảnh tối thứ 7 không",
        TutorChatFilters(), subjects, grades,
    ))
    f = routed["filters"]
    assert f.get("available_days") == [6], f"phải trích thứ 7 → [6], nhận: {f.get('available_days')}"
    assert (f.get("available_from") or "").startswith("18"), f"'tối' → 18:00, nhận {f.get('available_from')}"
    assert (f.get("available_to") or "").startswith("21"), f"'tối' → 21:00, nhận {f.get('available_to')}"


def test_ket_qua_chi_gom_gia_su_that_su_ranh():
    """Mọi gia sư bot trả về PHẢI nằm trong tập rảnh thật — đối chiếu thẳng với DB."""
    from app.services.tutoring_shared.candidates import _fetch_candidates
    from app.models.schemas import TutorChatContext

    days, start, end = SAT_EVENING
    expected = _tutors_free(days[0], *SAT_EVENING_UTC)
    if not expected:
        pytest.skip("Không có dữ liệu lịch cho khung này")

    filters = TutorChatFilters(
        subject_id=SUBJECT_VAT_LY, grade_level_id=GRADE_LOP_9,
        available_days=days, available_from=start, available_to=end,
    )
    content = asyncio.run(_fetch_candidates(
        TutorChatContext(), filters, "gia sư Vật lý lớp 9 rảnh tối thứ 7"))
    got = {t.get("tutorId") or t.get("tutor_id") for t in (content.get("tutors") or [])}

    thua = got - expected
    assert not thua, (
        f"{len(thua)} gia sư KHÔNG rảnh tối T7 vẫn lọt vào kết quả: {thua}. "
        f"Tập đúng theo DB: {len(expected)} người."
    )


def test_khong_neu_lich_thi_khong_loc():
    """Câu không nhắc lịch → 3 field lịch phải null (không tự bịa ràng buộc)."""
    subjects, grades = asyncio.run(_get_subjects()), asyncio.run(_get_grades())
    routed = asyncio.run(router_mod.route(
        [], "Tìm gia sư Vật lý lớp 9", TutorChatFilters(), subjects, grades))
    f = routed["filters"]
    assert not f.get("available_days")
    assert not f.get("available_from")
