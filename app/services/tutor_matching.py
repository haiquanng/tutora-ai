import math
from typing import Optional
from google import genai
from supabase import Client
from ..core.config import get_settings
from ..core.dependencies import get_supabase, get_gemini_client
from ..models.schemas import TutorRecommendResult

_settings = get_settings()

META_COLS = "tutor_id, city, district, teaching_mode, subject_ids, grades, price_min, price_max, average_rating, total_reviews, completed_hours"

# ─── Pha 3: scoring (đã thiết kế sẵn ở migration 20260618_tutor_vectors_add_meta.sql,
# implement ở đây). Thuần, không gọi DB -> unit-test độc lập với Supabase/Gemini.

# Bayesian average: kéo rating của gia sư ít review về trung bình hệ thống, tránh
# 1 review 5 sao thắng 500 review 4.8 sao, và tránh gia sư 0 review bị coi là rating=0.
_RATING_PRIOR_MEAN = 4.5   # trung bình rating toàn hệ thống (đo thực tế trên seed data)
_RATING_PRIOR_WEIGHT = 10  # "độ tin cậy tối thiểu" — cần ~10 review mới gần sát rating thật

# Trọng số blend (nhánh có query): cân bằng khớp nhu cầu (similarity) và chất lượng
# đã kiểm chứng (rating), kinh nghiệm là tín hiệu phụ. Chốt cùng user — xem PR/commit liên quan.
_W_SIMILARITY = 0.5
_W_RATING = 0.35
_W_EXPERIENCE = 0.15

_EXPERIENCE_LOG_CAP = 1000  # completed_hours ở mức này coi như "kinh nghiệm tối đa" (log-scale)


def _bayesian_rating(average_rating: Optional[float], total_reviews: Optional[int]) -> float:
    """Rating đã làm mượt theo số review. Trả về thang 0-5 (cùng thang average_rating gốc)."""
    r = average_rating or 0.0
    v = total_reviews or 0
    return (_RATING_PRIOR_WEIGHT * _RATING_PRIOR_MEAN + v * r) / (_RATING_PRIOR_WEIGHT + v)


def _experience_score(completed_hours: Optional[int]) -> float:
    """Normalize completed_hours -> [0, 1] theo log-scale (giờ dạy tăng nhanh lúc đầu,
    chậm dần -> log tránh gia sư nghìn giờ áp đảo tuyệt đối gia sư mới). Clamp 1.0 vì
    gia sư vượt _EXPERIENCE_LOG_CAP vẫn chỉ nên coi là "kinh nghiệm tối đa", không hơn."""
    hours = max(completed_hours or 0, 0)
    return min(math.log1p(hours) / math.log1p(_EXPERIENCE_LOG_CAP), 1.0)


def score_tutor(
    *,
    similarity: Optional[float] = None,
    average_rating: Optional[float] = None,
    total_reviews: Optional[int] = None,
    completed_hours: Optional[int] = None,
) -> float:
    """Điểm cuối cùng để xếp hạng gia sư. `similarity=None` -> nhánh không-query,
    chỉ dùng rating (đã Bayesian-smooth) + kinh nghiệm, bỏ qua w_similarity."""
    rating_score = _bayesian_rating(average_rating, total_reviews) / 5.0  # normalize 0-1
    experience_score = _experience_score(completed_hours)

    if similarity is None:
        # Không có query -> re-normalize 2 trọng số còn lại để tổng vẫn = 1.
        w_sum = _W_RATING + _W_EXPERIENCE
        return (_W_RATING * rating_score + _W_EXPERIENCE * experience_score) / w_sum

    return (_W_SIMILARITY * similarity + _W_RATING * rating_score + _W_EXPERIENCE * experience_score)


def _build_results(tutor_ids: list[str], similarity_map: dict, meta_map: dict) -> list[TutorRecommendResult]:
    results = []
    for tid in tutor_ids:
        meta = meta_map.get(tid, {})
        results.append(TutorRecommendResult(
            tutor_id=tid,
            similarity=round(similarity_map.get(tid, 0.0), 4),
            city=meta.get("city"),
            district=meta.get("district"),
            teaching_mode=meta.get("teaching_mode"),
            subject_ids=meta.get("subject_ids"),
            grades=meta.get("grades"),
            price_min=meta.get("price_min"),
            price_max=meta.get("price_max"),
            average_rating=meta.get("average_rating"),
            total_reviews=meta.get("total_reviews"),
            completed_hours=meta.get("completed_hours"),
        ))
    return results


async def match_tutors(
    query: Optional[str],
    candidate_ids: Optional[list[str]],
    top_k: int = 10,
) -> list[TutorRecommendResult]:
    sb: Client = get_supabase()

    # Normalize inputs
    ids = candidate_ids if candidate_ids else None
    has_query = bool(query and query.strip())

    # --- No query: skip embedding, rank candidates bằng Pha 3 scoring (Bayesian rating
    # + kinh nghiệm). PHẢI fetch metadata cho TOÀN BỘ ids rồi mới sort+cắt top_k — cắt
    # trước khi sort (như code cũ) làm mất tác dụng của việc rank theo rating.
    if not has_query:
        if not ids:
            return []
        meta_rows = (
            sb.table("tutor_vectors")
            .select(META_COLS)
            .in_("tutor_id", ids)
            .execute()
            .data or []
        )
        meta_map = {r["tutor_id"]: r for r in meta_rows}
        sorted_ids = sorted(
            ids,
            key=lambda tid: score_tutor(
                average_rating=meta_map.get(tid, {}).get("average_rating"),
                total_reviews=meta_map.get(tid, {}).get("total_reviews"),
                completed_hours=meta_map.get(tid, {}).get("completed_hours"),
            ),
            reverse=True,
        )[:top_k]
        return _build_results(sorted_ids, {}, meta_map)

    # --- Has query: embed + vector-rank rồi blend similarity/rating/kinh nghiệm (Pha 3).
    # Over-fetch (top_k * OVER_FETCH) từ RPC trước khi scoring lại — nếu chỉ lấy đúng
    # top_k theo similarity thuần, gia sư rating cao nhưng similarity hơi thấp có thể
    # đã bị loại trước khi kịp tính điểm tổng hợp.
    gemini_client: genai.Client = get_gemini_client()
    result = gemini_client.models.embed_content(
        model="gemini-embedding-2",
        contents=query,
        config={"output_dimensionality": _settings.rag_embedding_dim},
    )
    embedding = result.embeddings[0].values

    OVER_FETCH = 3
    rows = sb.rpc("match_tutors", {
        "query_embedding": embedding,
        "match_count": top_k * OVER_FETCH,
        "filter_ids": ids,
    }).execute().data or []

    if not rows:
        return []

    candidate_ids = [r["tutor_id"] for r in rows]
    similarity_map = {r["tutor_id"]: r["similarity"] for r in rows}

    meta_rows = (
        sb.table("tutor_vectors")
        .select(META_COLS)
        .in_("tutor_id", candidate_ids)
        .execute()
        .data or []
    )
    meta_map = {r["tutor_id"]: r for r in meta_rows}

    ranked_ids = sorted(
        candidate_ids,
        key=lambda tid: score_tutor(
            similarity=similarity_map.get(tid, 0.0),
            average_rating=meta_map.get(tid, {}).get("average_rating"),
            total_reviews=meta_map.get(tid, {}).get("total_reviews"),
            completed_hours=meta_map.get(tid, {}).get("completed_hours"),
        ),
        reverse=True,
    )[:top_k]

    return _build_results(ranked_ids, similarity_map, meta_map)
