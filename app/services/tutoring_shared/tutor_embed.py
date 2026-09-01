"""
Vector hoá 1 gia sư (event-driven) → bảng tutor_embeddings trên DB dùng chung.

tutor_embeddings vừa là vector index (cột embedding, cho match_tutors) vừa mang metadata
(rating/giá/môn — cho scoring). content_hash = sha256 của source text; embedding khớp hash
nào thì lưu hash đó để lần sau biết có cần embed lại không (giống question bank).
"""
from __future__ import annotations

import hashlib

from google import genai

from ...core.config import get_settings
from ...core.dependencies import get_supabase, get_gemini_client
from ..telemetry.usage import track

_settings = get_settings()
GEMINI_EMBED_MODEL = "gemini-embedding-2"


def _build_source_text(profile: dict, facts: dict | None = None) -> str:
    """Text để embed, CÓ TRỌNG SỐ theo độ tin cậy của nguồn.

    Bài học từ hồ sơ 22f76a69: bio nói "gia sư Tiếng Anh lớp 7-8", headline nói "Hóa học
    THCS", nhưng prices (thứ gia sư THẬT SỰ cam kết dạy, đã được duyệt) là Toán lớp 12
    → vector xây từ text tự do khiến tìm "toán 12" KHÔNG ra người này.

    Nên xếp theo độ tin cậy: dữ liệu NGHIỆP VỤ (môn/lớp/hình thức/khu vực từ bảng prices)
    là nguồn sự thật → đặt ĐẦU và LẶP LẠI để chi phối vector; text marketing do gia sư tự
    viết (có thể cũ/sai/copy-paste) xuống sau. Embedding nhạy với thứ tự + tần suất nên
    đây là cách tăng trọng số mà không phải đổi schema sang nhiều vector.
    """
    facts = facts or {}
    parts: list[str] = []

    # (1) NẶNG NHẤT — dữ liệu nghiệp vụ, lặp 2 lần (đầu + cuối phần fact).
    subject_names = facts.get("subject_names") or []
    grade_names = facts.get("grade_names") or []
    core = []
    if subject_names:
        core.append(f"Môn dạy: {', '.join(subject_names)}")
    if grade_names:
        core.append(f"Khối lớp: {', '.join(grade_names)}")
    if core:
        parts.append(". ".join(core))

    # (2) Học vấn — dữ liệu factual, user hay tìm theo ("thạc sĩ", "sư phạm", tên trường).
    if profile.get("education"):
        parts.append(f"Học vấn: {profile['education'].strip()}")

    mode = {"online": "Dạy online", "offline": "Dạy tại nhà", "both": "Dạy online và tại nhà"}.get(
        str(profile.get("teaching_mode") or "").lower())
    if mode:
        parts.append(mode)
    if profile.get("teaching_area_city"):
        parts.append(f"Khu vực: {profile['teaching_area_city']}")

    # (3) KHÔNG lặp lại môn/lớp. Pool ứng viên đã được SQL lọc CỨNG theo môn+lớp trước khi
    # tới vector, nên mọi người trong pool đều thoả — nhấn thêm chỉ làm ai cũng giống ai và
    # DÌM tín hiệu phân biệt thật (bằng cấp, kinh nghiệm, kiểu học sinh).
    # Đo được: khi lặp 2 lần, gia sư DUY NHẤT có "Thủ Khoa Kỹ Thuật Phần Mềm" chỉ đạt
    # similarity 0.7027 — THẤP NHẤT pool Vật lý lớp 7, thua người không liên quan (0.8041).
    # Môn/lớp vẫn giữ 1 lần ở (1) để lo ca bio ghi lệch môn (bio nói Tiếng Anh mà dạy Toán).

    # (4) NHẸ NHẤT — text gia sư tự viết, xuống cuối.
    for k in ("headline", "bio", "experience"):
        if profile.get(k):
            parts.append(profile[k].strip())

    return ". ".join(parts)


def _hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _load_facts(sb, meta: dict) -> dict:
    """Đổi subject_ids/grades (id) → TÊN môn/lớp cho _build_source_text.

    Vector cần TÊN ("Toán Học", "Lớp 12") vì user gõ tên, không gõ id. Tách khỏi _load_meta
    vì dict của hàm đó được ghi thẳng vào bảng tutor_embeddings — thêm key lạ sẽ lỗi insert.
    """
    subject_names: list[str] = []
    grade_names: list[str] = []
    try:
        sids = meta.get("subject_ids") or []
        if sids:
            rows = sb.table("subjects").select("subject_id, subject_name").in_(
                "subject_id", list(sids)).execute().data or []
            subject_names = [r["subject_name"] for r in rows if r.get("subject_name")]
        gids = [int(g) for g in (meta.get("grades") or []) if str(g).isdigit()]
        if gids:
            rows = sb.table("grade_levels").select("grade_level_id, grade_name").in_(
                "grade_level_id", gids).execute().data or []
            grade_names = [r["grade_name"] for r in rows if r.get("grade_name")]
    except Exception as e:
        # Không có tên môn/lớp thì vector vẫn dựng được từ text — không chặn embed.
        print(f"_load_facts error: {e}")
    return {"subject_names": subject_names, "grade_names": grade_names}


def _load_meta(sb, tutor_id: str, profile: dict) -> dict:
    """subject_ids/grades/price_min/max từ prices + rating/hours/khu vực từ profile."""
    prices = (
        sb.table("tutor_subject_grade_prices")
        .select("subject_id, grade_level_id, price_per_hour")
        .eq("tutor_id", tutor_id)
        .eq("is_active", True)
        .execute()
        .data or []
    )
    subjects = {p["subject_id"] for p in prices if p.get("subject_id")}
    grades = {str(p["grade_level_id"]) for p in prices if p.get("grade_level_id")}
    amounts = [float(p["price_per_hour"]) for p in prices if p.get("price_per_hour") is not None]
    return {
        "subject_ids": list(subjects) or None,
        "grades": list(grades) or None,
        "price_min": min(amounts) if amounts else None,
        "price_max": max(amounts) if amounts else None,
        "city": profile.get("teaching_area_city"),
        "district": profile.get("teaching_area_district"),
        "teaching_mode": profile.get("teaching_mode"),
        "average_rating": profile.get("average_rating"),
        "total_reviews": profile.get("total_reviews"),
        "completed_hours": profile.get("completed_hours"),
    }


def _embed(text: str) -> list[float]:
    gemini: genai.Client = get_gemini_client()
    with track("tutor_profile_embed", GEMINI_EMBED_MODEL) as _t:
        result = _t.done(gemini.models.embed_content(
            model=GEMINI_EMBED_MODEL,
            contents=text,
            config={"output_dimensionality": _settings.rag_embedding_dim},
        ))
    return result.embeddings[0].values


def embed_tutor(tutor_id: str) -> dict:
    """Vector hoá / cập nhật 1 gia sư. Idempotent:
      - content đổi (hash khác) → embed lại + upsert cả embedding lẫn metadata.
      - content không đổi        → chỉ update metadata (rating/giá) — bỏ qua Gemini.
      - profile không tồn tại / không active-public → xoá vector (gỡ khỏi kết quả tìm).
    Trả dict trạng thái để .NET/log biết đã làm gì.
    """
    sb = get_supabase()

    profile = (
        sb.table("tutor_profiles")
        .select(
            "tutor_id, headline, bio, education, experience, teaching_area_city, "
            "teaching_area_district, teaching_mode, average_rating, total_reviews, "
            "completed_hours, profile_status, is_public"
        )
        .eq("tutor_id", tutor_id)
        .limit(1)
        .execute()
        .data
    )
    if not profile:
        sb.table("tutor_embeddings").delete().eq("tutor_id", tutor_id).execute()
        return {"tutor_id": tutor_id, "action": "deleted", "reason": "profile not found"}
    profile = profile[0]

    # Chỉ index gia sư đang hiển thị công khai — không thì gỡ vector (giống sweep cũ).
    if profile.get("profile_status") != "active" or not profile.get("is_public"):
        sb.table("tutor_embeddings").delete().eq("tutor_id", tutor_id).execute()
        return {"tutor_id": tutor_id, "action": "deleted", "reason": "not active/public"}

    # meta TRƯỚC source_text: text embed giờ gồm cả môn/lớp (nguồn sự thật từ prices),
    # nên phải có meta rồi mới dựng được text. Hash cũng đổi theo môn/lớp → gia sư thêm/bỏ
    # môn là tự re-embed, không còn cảnh vector nói môn A mà thực tế dạy môn B.
    meta = _load_meta(sb, tutor_id, profile)
    facts = _load_facts(sb, meta)
    source_text = _build_source_text(profile, facts)
    new_hash = _hash(source_text) if source_text else None

    existing = (
        sb.table("tutor_embeddings")
        .select("tutor_id, content_hash")
        .eq("tutor_id", tutor_id)
        .limit(1)
        .execute()
        .data
    )
    old_hash = existing[0]["content_hash"] if existing else None

    # Content không đổi + đã có vector → chỉ refresh metadata (khỏi tốn 1 lần embed).
    if existing and new_hash == old_hash:
        sb.table("tutor_embeddings").update(meta).eq("tutor_id", tutor_id).execute()
        return {"tutor_id": tutor_id, "action": "metadata_only"}

    # Không có text để embed (profile trống) → vẫn upsert metadata, embedding null.
    row = {"tutor_id": tutor_id, "content_hash": new_hash, **meta}
    if new_hash:
        row["embedding"] = _embed(source_text)
    sb.table("tutor_embeddings").upsert(row).execute()
    return {"tutor_id": tutor_id, "action": "embedded" if new_hash else "metadata_only"}
