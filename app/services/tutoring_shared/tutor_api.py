"""
Đọc dữ liệu CÔNG KHAI của gia sư qua API .NET (không đọc DB thẳng).

VÌ SAO QUA API, KHÔNG QUERY SUPABASE:
- Điều kiện "hồ sơ được hiển thị công khai" là BUSINESS RULE của .NET
  (IsPubliclyVisibleTutorProfile = profile_status active + is_public + is_accepting_bookings).
  Bản đọc DB thẳng trước đây chỉ check 2 điều kiện đầu → BỎ SÓT gia sư đã tự tắt nhận
  booking, chatbot vẫn khoe hồ sơ đó cho khách. Nhân bản rule = sớm muộn cũng lệch.
- /full-profile là 1 call thay cho 6 query tuần tự, lại có sẵn certificates (đã verified),
  feedbacks và teaching stats — những thứ bản đọc DB không lấy được.
- Cùng nguồn với trang /tutor-detail trên web → bot và trang chi tiết luôn nói giống nhau.

CHỈ ĐỌC, CHỈ ENDPOINT CÔNG KHAI ([AllowAnonymous]). Mọi thứ cần danh tính người dùng
(students, booking-eligibility) hay ghi dữ liệu (bookings, payment) KHÔNG thuộc file này:
tutora-ai không cầm JWT của user, và tiền/đặt lịch là việc của code tất định, không phải LLM.
"""
from __future__ import annotations

import httpx

from ...core.config import get_settings

_settings = get_settings()

_TIMEOUT = 20


async def _get(path: str) -> dict | list | None:
    """GET .NET, bóc APIResponse { content }. Lỗi/404 → None (caller tự có câu xin lỗi)."""
    url = f"{_settings.dotnet_be_url.rstrip('/')}{path}"
    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
            r = await client.get(url, headers={"Accept": "application/json"})
            if r.status_code == 404:
                return None
            r.raise_for_status()
            data = r.json()
    except Exception as e:
        print(f"tutor_api GET {path} error: {e}")
        return None
    if isinstance(data, dict):
        return data.get("content", data)
    return data


async def get_full_profile(tutor_id: str) -> dict | None:
    """Hồ sơ công khai đầy đủ: thông tin cơ bản, học vấn, chứng chỉ đã duyệt, bảng giá
    theo môn/lớp, lịch rảnh, đánh giá + thống kê, số buổi/số học sinh đã dạy.

    None = không tồn tại HOẶC không đủ điều kiện hiển thị công khai (.NET trả 404). Đây
    chính là chỗ ta MƯỢN rule của .NET thay vì tự viết lại."""
    if not tutor_id:
        return None
    data = await _get(f"/api/tutors/{tutor_id}/full-profile")
    return data if isinstance(data, dict) else None


async def get_schedule(tutor_id: str) -> dict | None:
    """Lịch rảnh + gói học TƯƠI (full-profile cache 20 phút). Dùng khi user hỏi thẳng
    về lịch — cùng lý do FE gọi riêng endpoint này thay vì tin field trong full-profile."""
    if not tutor_id:
        return None
    data = await _get(f"/api/tutors/{tutor_id}/schedule")
    return data if isinstance(data, dict) else None
