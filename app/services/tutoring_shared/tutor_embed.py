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

_settings = get_settings()
GEMINI_EMBED_MODEL = "gemini-embedding-2"


def _build_source_text(profile: dict) -> str:
    """Text để embed: headline + bio + học vấn + kinh nghiệm (giống seed cũ)."""
    parts = []
    if profile.get("headline"):
        parts.append(profile["headline"].strip())
    if profile.get("bio"):
        parts.append(profile["bio"].strip())
    if profile.get("education"):
        parts.append(f"Học vấn: {profile['education'].strip()}")
    if profile.get("experience"):
        parts.append(f"Kinh nghiệm: {profile['experience'].strip()}")
    return ". ".join(parts)


def _hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


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
    result = gemini.models.embed_content(
        model=GEMINI_EMBED_MODEL,
        contents=text,
        config={"output_dimensionality": _settings.rag_embedding_dim},
    )
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

    source_text = _build_source_text(profile)
    new_hash = _hash(source_text) if source_text else None
    meta = _load_meta(sb, tutor_id, profile)

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
