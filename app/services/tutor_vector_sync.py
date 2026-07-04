"""
Sync metadata tutor_vectors (DB pgvector) từ DB nghiệp vụ (.NET sở hữu).

VÌ SAO CẦN: tutor_vectors là DẪN XUẤT (derived index) — rating/reviews/giá/môn
được copy từ tutor_profiles + tutor_subject_grade_prices lúc seed. Không sync
định kỳ thì Ranking Core chấm điểm trên số liệu cũ dần (đã từng gây bug 42%
gia sư thiếu subject_ids do script sync tay bị gãy âm thầm).

Job này chạy nền trong FastAPI (lifespan), mỗi SYNC_INTERVAL_HOURS:
  1. Cập nhật metadata mọi vector từ nguồn sự thật (phân trang, không dính limit 1000).
  2. XOÁ vector của gia sư không còn active/public — tránh gia sư chưa duyệt
     lọt vào kết quả vector search.
Chỉ chạy khi SUPABASE_DEV_URL/KEY được cấu hình; không có thì bỏ qua (log warning).

KHÔNG re-embed ở đây — bio/headline đổi cần embedding mới, việc đó vẫn thuộc
scripts/seed_tutor_vectors.py (chạy tay/CI, có rate-limit Gemini riêng).
"""
from __future__ import annotations

import asyncio

from supabase import Client

from ..core.dependencies import get_supabase, get_supabase_dev

SYNC_INTERVAL_HOURS = 24
_PAGE_SIZE = 1000


def _fetch_all(query_builder, page_size: int = _PAGE_SIZE) -> list[dict]:
    """PostgREST giới hạn 1000 rows/query -> phân trang đến hết."""
    rows: list[dict] = []
    start = 0
    while True:
        page = query_builder.range(start, start + page_size - 1).execute().data or []
        rows.extend(page)
        if len(page) < page_size:
            return rows
        start += page_size


def _load_source_meta(sb_dev: Client) -> dict[str, dict]:
    """Đọc nguồn sự thật -> map tutor_id -> metadata cho tutor_vectors."""
    prices = _fetch_all(
        sb_dev.table("tutor_subject_grade_prices")
        .select("tutor_id, subject_id, grade_level_id, price_per_hour")
        .eq("is_active", True)
    )
    subject_map: dict[str, set] = {}
    grades_map: dict[str, set] = {}
    price_map: dict[str, list] = {}
    for p in prices:
        tid = p["tutor_id"]
        if p.get("subject_id"):
            subject_map.setdefault(tid, set()).add(p["subject_id"])
        if p.get("grade_level_id"):
            grades_map.setdefault(tid, set()).add(str(p["grade_level_id"]))
        if p.get("price_per_hour") is not None:
            price_map.setdefault(tid, []).append(float(p["price_per_hour"]))

    profiles = _fetch_all(
        sb_dev.table("tutor_profiles")
        .select("tutor_id, teaching_area_city, teaching_area_district, average_rating, "
                "total_reviews, completed_hours, teaching_mode")
        .eq("profile_status", "active")
        .eq("is_public", True)
    )

    meta: dict[str, dict] = {}
    for r in profiles:
        tid = r["tutor_id"]
        p = price_map.get(tid, [])
        meta[tid] = {
            "subject_ids": list(subject_map.get(tid, [])) or None,
            "grades": list(grades_map.get(tid, [])) or None,
            "price_min": min(p) if p else None,
            "price_max": max(p) if p else None,
            "city": r.get("teaching_area_city"),
            "district": r.get("teaching_area_district"),
            "teaching_mode": r.get("teaching_mode"),
            "average_rating": r.get("average_rating"),
            "total_reviews": r.get("total_reviews"),
            "completed_hours": r.get("completed_hours"),
        }
    return meta


def sync_tutor_vectors_once() -> dict:
    """Chạy 1 lần sync (sync, blocking — gọi qua asyncio.to_thread từ job nền).
    Trả về thống kê {updated, removed, skipped} để log/giám sát."""
    sb_dev = get_supabase_dev()
    if sb_dev is None:
        return {"skipped": True, "reason": "SUPABASE_DEV_URL/KEY chưa cấu hình"}

    sb_ai = get_supabase()
    source_meta = _load_source_meta(sb_dev)

    vec_rows = _fetch_all(sb_ai.table("tutor_vectors").select("tutor_id"))
    vec_ids = [r["tutor_id"] for r in vec_rows]

    updated = 0
    for tid in vec_ids:
        if tid not in source_meta:
            continue
        sb_ai.table("tutor_vectors").update(source_meta[tid]).eq("tutor_id", tid).execute()
        updated += 1

    # Gia sư có vector nhưng không còn active/public bên nguồn -> xoá khỏi index
    # (tránh gia sư chưa duyệt/đã khoá lọt vào vector search khi filter_ids=null).
    stale_ids = [tid for tid in vec_ids if tid not in source_meta]
    if stale_ids:
        sb_ai.table("tutor_vectors").delete().in_("tutor_id", stale_ids).execute()

    return {"updated": updated, "removed": len(stale_ids), "total_vectors": len(vec_ids)}


async def tutor_vector_sync_loop() -> None:
    """Job nền: sync ngay khi khởi động rồi lặp mỗi SYNC_INTERVAL_HOURS."""
    while True:
        try:
            stats = await asyncio.to_thread(sync_tutor_vectors_once)
            print(f"tutor_vector_sync: {stats}")
        except Exception as e:
            # Không để job chết vì 1 lần lỗi (DB tạm mất kết nối...) — lần sau thử lại.
            print(f"tutor_vector_sync error: {e}")
        await asyncio.sleep(SYNC_INTERVAL_HOURS * 3600)
