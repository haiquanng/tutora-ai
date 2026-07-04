"""
Đồng bộ metadata tutor_vectors (DB pgvector, DB-B) từ DB nghiệp vụ (.NET, DB-A).

tutor_vectors là DẪN XUẤT (derived index): rating/reviews/giá/môn là bản sao của
tutor_profiles + tutor_subject_grade_prices bên DB-A, dùng cho Ranking Core. Không
đồng bộ thì ranking chấm điểm trên số liệu cũ (đã từng gây bug 42% NULL subject_ids
do script sync tay gãy âm thầm).

KIẾN TRÚC — hybrid "fast-path + reconciliation sweep" (chuẩn ngành cho search index,
xem Kleppmann DDIA ch.11-12; Elasticsearch/Jira ghép streaming update + periodic
full reindex). Hai job chạy song song trên cùng bảng tutor_vectors:

  1. FAST-PATH (incremental poll, mỗi FAST_PATH_MINUTES phút):
     Đọc DB-A "mọi row updated_at > high-water mark" -> chỉ gia sư VỪA đổi (thường
     0 dòng) -> upsert vào DB-B. Độ trễ ~phút, chi phí O(Δ) gần 0. TỰ HỎI (pull),
     .NET không cần biết tutora-ai tồn tại -> không có hazard dual-write.
     ĐIỂM MÙ: updated_at polling KHÔNG phát hiện được xoá (row biến mất khỏi query).

  2. SWEEP (full reconciliation, mỗi SWEEP_INTERVAL_HOURS giờ, chạy đêm):
     Đọc TOÀN BỘ DB-A, so key-set với DB-B -> sửa mọi sai lệch fast-path bỏ sót +
     XOÁ vector của gia sư không còn active/public (bù điểm mù của fast-path). Đây
     là SÀN ĐÚNG ĐẮN — fast-path được phép lossy vì sweep luôn chữa lại trong <=24h.

Chỉ chạy khi SUPABASE_DEV_URL/KEY được cấu hình; không có thì bỏ qua (log warning).
KHÔNG re-embed ở đây — bio/headline đổi cần embedding mới, việc đó thuộc
scripts/seed_tutor_vectors.py (chạy tay/CI, có rate-limit Gemini riêng).
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone

from supabase import Client

from ..core.dependencies import get_supabase, get_supabase_dev

FAST_PATH_MINUTES = 2      # độ trễ tối đa của đường chính; poll nhẹ, gần như luôn 0 dòng
SWEEP_INTERVAL_HOURS = 6   # lưới an toàn: bắt update fast-path sót + xoá orphan
_PAGE_SIZE = 1000
# Trừ lùi high-water mark 1 chút để không bỏ sót transaction commit-muộn có
# updated_at nhỏ hơn thời điểm ta đọc (clock/commit skew). Upsert idempotent nên
# đọc trùng vô hại.
_HWM_OVERLAP_SECONDS = 30
_SYNC_KEY = "tutor_vectors"  # 1 mốc chung cho cả profiles+prices (lấy max của cả hai)


# ───────────────────────── ĐỌC NGUỒN (DB-A) ─────────────────────────
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


def _load_prices_map(sb_dev: Client, tutor_ids: list[str] | None = None) -> tuple[dict, dict, dict]:
    """subject_ids/grades/price cho các tutor_id (None = toàn bộ)."""
    q = (sb_dev.table("tutor_subject_grade_prices")
         .select("tutor_id, subject_id, grade_level_id, price_per_hour")
         .eq("is_active", True))
    if tutor_ids is not None:
        q = q.in_("tutor_id", tutor_ids)
    prices = _fetch_all(q)

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
    return subject_map, grades_map, price_map


def _build_meta(profile: dict, subject_map: dict, grades_map: dict, price_map: dict) -> dict:
    tid = profile["tutor_id"]
    p = price_map.get(tid, [])
    return {
        "tutor_id": tid,
        "subject_ids": list(subject_map.get(tid, [])) or None,
        "grades": list(grades_map.get(tid, [])) or None,
        "price_min": min(p) if p else None,
        "price_max": max(p) if p else None,
        "city": profile.get("teaching_area_city"),
        "district": profile.get("teaching_area_district"),
        "teaching_mode": profile.get("teaching_mode"),
        "average_rating": profile.get("average_rating"),
        "total_reviews": profile.get("total_reviews"),
        "completed_hours": profile.get("completed_hours"),
    }


_PROFILE_COLS = ("tutor_id, teaching_area_city, teaching_area_district, average_rating, "
                 "total_reviews, completed_hours, teaching_mode, updated_at, profile_status, is_public")


# ───────────────────────── HIGH-WATER MARK (DB-B) ─────────────────────────
def _get_hwm(sb_ai: Client) -> str | None:
    rows = sb_ai.table("sync_state").select("last_synced_at").eq("sync_key", _SYNC_KEY).execute().data or []
    return rows[0]["last_synced_at"] if rows else None


def _set_hwm(sb_ai: Client, ts: str) -> None:
    sb_ai.table("sync_state").upsert({
        "sync_key": _SYNC_KEY,
        "last_synced_at": ts,
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }).execute()


def _minus_overlap(ts_iso: str) -> str:
    from datetime import timedelta
    dt = datetime.fromisoformat(ts_iso)
    return (dt - timedelta(seconds=_HWM_OVERLAP_SECONDS)).isoformat()


# ───────────────────────── FAST-PATH (incremental) ─────────────────────────
def fast_path_sync_once() -> dict:
    """Đọc gia sư đổi kể từ high-water mark -> upsert DB-B. Chỉ update metadata
    (không xoá — xoá là việc của sweep). Trả thống kê để log."""
    sb_dev = get_supabase_dev()
    if sb_dev is None:
        return {"skipped": True, "reason": "SUPABASE_DEV_URL/KEY chưa cấu hình"}
    sb_ai = get_supabase()

    hwm = _get_hwm(sb_ai)
    q = sb_dev.table("tutor_profiles").select(_PROFILE_COLS)
    if hwm:
        q = q.gt("updated_at", _minus_overlap(hwm))
    # Lần đầu (chưa có hwm) -> KHÔNG kéo toàn bộ ở fast-path; để sweep lo full sync,
    # fast-path chỉ set mốc = now để bắt đầu theo dõi thay đổi từ đây.
    changed = _fetch_all(q.order("updated_at")) if hwm else []

    if not hwm:
        _set_hwm(sb_ai, datetime.now(timezone.utc).isoformat())
        return {"bootstrap": True, "note": "hwm khởi tạo; sweep sẽ full-sync"}

    if not changed:
        return {"updated": 0}

    active = [p for p in changed if p.get("profile_status") == "active" and p.get("is_public")]
    ids = [p["tutor_id"] for p in active]
    subject_map, grades_map, price_map = _load_prices_map(sb_dev, ids) if ids else ({}, {}, {})

    # Chỉ upsert gia sư ĐÃ CÓ vector (fast-path không tạo vector mới vì chưa có
    # embedding — gia sư mới do seed_tutor_vectors.py xử lý). Lọc trước để tránh
    # insert row thiếu cột embedding (NOT NULL).
    existing = _fetch_all(sb_ai.table("tutor_vectors").select("tutor_id").in_("tutor_id", ids)) if ids else []
    existing_ids = {r["tutor_id"] for r in existing}

    updated = 0
    for p in active:
        if p["tutor_id"] not in existing_ids:
            continue
        meta = _build_meta(p, subject_map, grades_map, price_map)
        sb_ai.table("tutor_vectors").update(meta).eq("tutor_id", meta["tutor_id"]).execute()
        updated += 1

    # Đẩy high-water mark = updated_at lớn nhất vừa xử lý.
    max_ts = max(p["updated_at"] for p in changed)
    _set_hwm(sb_ai, max_ts)
    return {"updated": updated, "seen": len(changed), "new_hwm": max_ts}


# ───────────────────────── SWEEP (full reconciliation) ─────────────────────────
def reconcile_sweep_once() -> dict:
    """Full reconcile: đồng bộ metadata mọi vector + XOÁ orphan (gia sư không còn
    active/public). Đây là sàn đúng đắn — bù điểm mù xoá của fast-path và bắt mọi
    update fast-path bỏ sót. Reset high-water mark về hiện tại sau khi xong."""
    sb_dev = get_supabase_dev()
    if sb_dev is None:
        return {"skipped": True, "reason": "SUPABASE_DEV_URL/KEY chưa cấu hình"}
    sb_ai = get_supabase()

    profiles = _fetch_all(
        sb_dev.table("tutor_profiles").select(_PROFILE_COLS)
        .eq("profile_status", "active").eq("is_public", True)
    )
    source = {p["tutor_id"]: p for p in profiles}
    subject_map, grades_map, price_map = _load_prices_map(sb_dev)

    vec_ids = [r["tutor_id"] for r in _fetch_all(sb_ai.table("tutor_vectors").select("tutor_id"))]

    updated = 0
    for tid in vec_ids:
        if tid not in source:
            continue
        meta = _build_meta(source[tid], subject_map, grades_map, price_map)
        sb_ai.table("tutor_vectors").update(meta).eq("tutor_id", tid).execute()
        updated += 1

    # Orphan: có vector bên B nhưng bên A không còn active/public -> xoá khỏi index.
    stale_ids = [tid for tid in vec_ids if tid not in source]
    if stale_ids:
        sb_ai.table("tutor_vectors").delete().in_("tutor_id", stale_ids).execute()

    _set_hwm(sb_ai, datetime.now(timezone.utc).isoformat())
    return {"updated": updated, "removed": len(stale_ids), "total_vectors": len(vec_ids)}


# ───────────────────────── JOB LOOPS (lifespan) ─────────────────────────
async def fast_path_loop() -> None:
    while True:
        try:
            stats = await asyncio.to_thread(fast_path_sync_once)
            if stats.get("updated") or stats.get("bootstrap"):
                print(f"tutor_vector fast-path: {stats}")
        except Exception as e:
            print(f"tutor_vector fast-path error: {e}")
        await asyncio.sleep(FAST_PATH_MINUTES * 60)


async def reconcile_sweep_loop() -> None:
    # Chạy sweep 1 lần lúc khởi động (full-sync ban đầu + set hwm), rồi lặp.
    while True:
        try:
            stats = await asyncio.to_thread(reconcile_sweep_once)
            print(f"tutor_vector sweep: {stats}")
        except Exception as e:
            print(f"tutor_vector sweep error: {e}")
        await asyncio.sleep(SWEEP_INTERVAL_HOURS * 3600)
