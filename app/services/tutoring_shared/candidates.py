"""
Hạ tầng gọi Ranking Core (.NET /recommend) + chuẩn hoá dữ liệu, DÙNG CHUNG cho cả
zalo_agent lẫn web_agent.
"""
from __future__ import annotations

import re

import httpx

from ...core.config import get_settings

_settings = get_settings()


# DANH SÁCH MÔN (cache process)
_subjects_cache: list[dict] = []

async def _get_subjects() -> list[dict]:
    """Lấy [{subjectId, subjectName}] từ .NET; cache trong process (đổi rất hiếm)."""
    global _subjects_cache
    if _subjects_cache:
        return _subjects_cache
    try:
        url = f"{_settings.dotnet_be_url}/api/subjects"
        async with httpx.AsyncClient(timeout=15) as client:
            r = await client.get(url, headers={"Accept": "application/json"})
            r.raise_for_status()
            data = r.json()
        _subjects_cache = data.get("content", data) or []
    except Exception as e:
        print(f"candidates subjects fetch error: {e}")
    return _subjects_cache


# DANH SÁCH LỚP (cache process)
_grades_cache: list[dict] = []


async def _get_grades() -> list[dict]:
    """Lấy [{gradeLevelId, gradeName}] từ .NET; cache trong process. Router cần để map
    'lớp 9' → gradeLevelId đúng (57, KHÔNG phải 9) — lọc lớp mới chính xác."""
    global _grades_cache
    if _grades_cache:
        return _grades_cache
    try:
        url = f"{_settings.dotnet_be_url}/api/grade-levels"
        async with httpx.AsyncClient(timeout=15) as client:
            r = await client.get(url, headers={"Accept": "application/json"})
            r.raise_for_status()
            data = r.json()
        _grades_cache = data.get("content", data) or []
    except Exception as e:
        print(f"candidates grades fetch error: {e}")
    return _grades_cache


# CHUẨN HOÁ TỈNH/THÀNH - todo
_CITY_PREFIX_RE = re.compile(r"^(Thành phố|Tỉnh)\s+", re.IGNORECASE)
_CITY_ALIASES = {
    "hồ chí minh": "TP.HCM",
    "sài gòn": "TP.HCM",
    "hcm": "TP.HCM",
    "tp hcm": "TP.HCM",
    "tp.hcm": "TP.HCM",
}


def _normalize_city(city: str | None) -> str | None:
    if not city:
        return city
    stripped = _CITY_PREFIX_RE.sub("", city).strip()
    return _CITY_ALIASES.get(stripped.lower(), stripped)


_GENDER_TO_INT = {"male": 1, "female": 2, "nam": 1, "nữ": 2, "nu": 2}


def _gender_to_int(gender) -> int | None:
    if gender is None:
        return None
    if isinstance(gender, int):
        return gender
    return _GENDER_TO_INT.get(str(gender).strip().lower())


# GỌI RANKING CORE (.NET /recommend)
async def _fetch_candidates(context, filters, query: str) -> dict:
    """Gọi .NET recommend (POST /api/tutors/recommend) — filter SQL + profile + rerank.
    subject_id từ filter (tích luỹ/đổi môn) ưu tiên hơn subject_id của context.
    `filters` là bất kỳ object có các thuộc tính subject_id/min_rate/max_rate/tutor_gender/
    desired_count (TutorChatFilters ở cả 2 kênh đều thoả)."""
    desired = getattr(filters, "desired_count", None)
    top_k = desired if desired and desired > 0 else 10
    payload = {
        "subjectId": filters.subject_id or context.subject_id,
        # filter tích luỹ (router trích "lớp 9"→id) ưu tiên hơn context (Zalo patch) —
        # web stateless chỉ có đường filter; getattr để zalo_agent (filter không có field
        # này) vẫn chạy, fallback context.
        "gradeLevelId": getattr(filters, "grade_level_id", None) or context.grade_level_id,
        "teachingMode": context.teaching_mode,
        "city": _normalize_city(context.city),
        "minRate": filters.min_rate,
        "maxRate": filters.max_rate,
        "gender": _gender_to_int(filters.tutor_gender or context.tutor_gender),
        "query": query or None,
        "topK": top_k,
    }
    # Lịch rảnh: chỉ web_agent có 3 field này. getattr để luồng Zalo (TutorChatFilters cũ
    # hoặc object khác) không đổi hành vi — không gửi field thì .NET bỏ qua, lọc như cũ.
    days = getattr(filters, "available_days", None)
    if days:
        payload["availableDaysOfWeek"] = days
    a_from = getattr(filters, "available_from", None)
    a_to = getattr(filters, "available_to", None)
    if a_from and a_to:
        payload["availableFrom"] = a_from
        payload["availableTo"] = a_to
    url = f"{_settings.dotnet_be_url}/api/tutors/recommend"
    async with httpx.AsyncClient(timeout=30) as client:
        r = await client.post(url, json=payload)
        r.raise_for_status()
        data = r.json()
    # .NET bọc trong { content: {...} }
    return data.get("content", data)
