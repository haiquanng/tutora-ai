import math
from typing import Optional
from google import genai
from supabase import Client
from ...core.config import get_settings
from ...core.dependencies import get_supabase, get_gemini_client
from ...models.schemas import TutorRecommendResult

_settings = get_settings()

META_COLS = "tutor_id, city, district, teaching_mode, subject_ids, grades, price_min, price_max, average_rating, total_reviews, completed_hours"

# Pha 3: scoring

# Bayesian average: kéo rating của gia sư ít review về trung bình hệ thống, tránh
# 1 review 5 sao thắng 500 review 4.8 sao, và tránh gia sư 0 review bị coi là rating=0.
_RATING_PRIOR_MEAN = 4.5   # trung bình rating toàn hệ thống
_RATING_PRIOR_WEIGHT = 10  # "độ tin cậy tối thiểu" — cần ~10 review mới gần sát rating thật

# Trọng số blend (nhánh có query): cân bằng khớp nhu cầu (similarity) và chất lượng
# đã kiểm chứng (rating), kinh nghiệm là tín hiệu phụ. Chốt cùng user — xem PR/commit liên quan.
_W_SIMILARITY = 0.40
_W_TEXT = 0.15        # full-text khớp nguyên văn (hybrid) — xem match_tutors_hybrid
_W_RATING = 0.30
_W_EXPERIENCE = 0.15

_W_SIMILARITY_SPECIFIC = 0.40
_W_TEXT_SPECIFIC = 0.35
_W_RATING_SPECIFIC = 0.15
_W_EXPERIENCE_SPECIFIC = 0.10

_EXPERIENCE_LOG_CAP = 1000  

_EXPERIENCE_FLOOR = 0.4


def _bayesian_rating(average_rating: Optional[float], total_reviews: Optional[int]) -> float:
    """Rating đã làm mượt theo số review. Trả về thang 0-5 (cùng thang average_rating gốc)."""
    r = average_rating or 0.0
    v = total_reviews or 0
    return (_RATING_PRIOR_WEIGHT * _RATING_PRIOR_MEAN + v * r) / (_RATING_PRIOR_WEIGHT + v)


def _experience_score(completed_hours: Optional[int]) -> float:
    """Normalize completed_hours -> [_EXPERIENCE_FLOOR, 1] theo log-scale (giờ dạy tăng nhanh
    lúc đầu, chậm dần -> log tránh gia sư nghìn giờ áp đảo tuyệt đối gia sư mới). Clamp 1.0 vì
    gia sư vượt _EXPERIENCE_LOG_CAP vẫn chỉ nên coi là "kinh nghiệm tối đa", không hơn.

    SÀN (prior) cho gia sư mới: 0 giờ dạy KHÔNG phải 0 điểm tuyệt đối — cùng tinh thần
    _bayesian_rating kéo 0 review về trung bình hệ thống thay vì coi như rating 0. Trước đây
    0 giờ = 0 điểm khiến gia sư mới mất trắng toàn bộ phần kinh nghiệm, không thể lọt top →
    không được ai thấy → không có buổi dạy nào → mãi 0 giờ. Vòng lặp chết, bất công hệ thống.
    """
    hours = max(completed_hours or 0, 0)
    raw = min(math.log1p(hours) / math.log1p(_EXPERIENCE_LOG_CAP), 1.0)
    # Map [0,1] -> [FLOOR,1]: giữ nguyên thứ tự giữa các gia sư, chỉ nâng sàn người mới.
    return _EXPERIENCE_FLOOR + (1.0 - _EXPERIENCE_FLOOR) * raw


def score_tutor(
    *,
    similarity: Optional[float] = None,
    average_rating: Optional[float] = None,
    total_reviews: Optional[int] = None,
    completed_hours: Optional[int] = None,
    specific_query: bool = False,
    text_rank: float = 0.0,
) -> float:
    """Điểm cuối cùng để xếp hạng gia sư. `similarity=None` -> nhánh không-query,
    chỉ dùng rating (đã Bayesian-smooth) + kinh nghiệm, bỏ qua w_similarity.

    `specific_query=True` (user nêu tiêu chí ngữ nghĩa cụ thể) -> ưu tiên similarity mạnh
    hơn, để người khớp CHÍNH XÁC nhu cầu không bị rating/giờ dạy dìm xuống.
    """
    rating_score = _bayesian_rating(average_rating, total_reviews) / 5.0  # normalize 0-1
    experience_score = _experience_score(completed_hours)

    if similarity is None:
        # Không có query -> re-normalize 2 trọng số còn lại để tổng vẫn = 1.
        w_sum = _W_RATING + _W_EXPERIENCE
        return (_W_RATING * rating_score + _W_EXPERIENCE * experience_score) / w_sum

    if specific_query:
        # Query có tiêu chí cụ thể → full-text (khớp nguyên văn bằng cấp/trường/chứng chỉ)
        # là tín hiệu ĐÁNG TIN NHẤT, vì đó chính là cái vector làm không nổi.
        return (_W_SIMILARITY_SPECIFIC * similarity
                + _W_TEXT_SPECIFIC * text_rank
                + _W_RATING_SPECIFIC * rating_score
                + _W_EXPERIENCE_SPECIFIC * experience_score)

    return (_W_SIMILARITY * similarity
            + _W_TEXT * text_rank
            + _W_RATING * rating_score
            + _W_EXPERIENCE * experience_score)


# Dấu hiệu user nêu tiêu chí NGOÀI môn/lớp (bằng cấp, trường, chứng chỉ, kiểu học sinh,
# mục tiêu, tính cách). Query nào cũng có môn/lớp nên chỉ riêng chúng KHÔNG tính là cụ thể.
_SPECIFIC_HINTS = (
    "thạc sĩ", "thac si", "tiến sĩ", "tien si", "cử nhân", "cu nhan", "master",
    "thủ khoa", "thu khoa", "á khoa", "a khoa", "giỏi", "xuất sắc", "xuat sac",
    "kỹ sư", "ky su", "giáo viên", "giao vien", "trình độ", "trinh do",
    "sư phạm", "su pham", "bằng", "bang cap", "chứng chỉ", "chung chi",
    "đại học", "dai hoc", "trường", "ielts", "toeic", "sat", "gpa",
    "kinh nghiệm", "kinh nghiem", "mất gốc", "mat goc", "nâng cao", "nang cao",
    "luyện thi", "luyen thi", "ôn thi", "on thi", "chuyên", "chuyen",
    "kiên nhẫn", "kien nhan", "nhiệt tình", "nhiet tinh", "vui vẻ", "vui ve",
)


def _is_specific_query(query: Optional[str]) -> bool:
    """Query có tiêu chí ngữ nghĩa ngoài môn/lớp không → chọn bộ trọng số ưu tiên similarity."""
    if not query:
        return False
    q = query.lower()
    return any(h in q for h in _SPECIFIC_HINTS)


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
            sb.table("tutor_embeddings")
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
    # Hybrid: lấy CẢ cosine similarity (gần nghĩa) lẫn full-text rank (khớp nguyên văn).
    # Vector một mình không tra được chuỗi hiếm chính xác ("Thủ Khoa Kỹ Thuật Phần Mềm" →
    # người DUY NHẤT có đúng chuỗi đó lại có similarity THẤP NHẤT pool vì bio dài pha loãng).
    # Fallback về match_tutors nếu RPC hybrid chưa được migrate (deploy lệch nhau).
    try:
        rows = sb.rpc("match_tutors_hybrid", {
            "query_embedding": embedding,
            "query_text": query,
            "match_count": top_k * OVER_FETCH,
            "filter_ids": ids,
        }).execute().data or []
    except Exception as e:
        print(f"match_tutors_hybrid unavailable, fallback vector-only: {e}")
        rows = sb.rpc("match_tutors", {
            "query_embedding": embedding,
            "match_count": top_k * OVER_FETCH,
            "filter_ids": ids,
        }).execute().data or []

    if not rows:
        return []

    candidate_ids = [r["tutor_id"] for r in rows]
    similarity_map = {r["tutor_id"]: r["similarity"] for r in rows}
    # ts_rank_cd không có thang cố định → chuẩn hoá theo max trong pool để blend được với
    # similarity (0..1). Pool rỗng text_rank (query không khớp từ nào) → tất cả 0, vô hại.
    raw_text = {r["tutor_id"]: (r.get("text_rank") or 0.0) for r in rows}
    max_text = max(raw_text.values(), default=0.0)
    text_map = ({k: v / max_text for k, v in raw_text.items()} if max_text > 0
                else {k: 0.0 for k in raw_text})

    meta_rows = (
        sb.table("tutor_embeddings")
        .select(META_COLS)
        .in_("tutor_id", candidate_ids)
        .execute()
        .data or []
    )
    meta_map = {r["tutor_id"]: r for r in meta_rows}

    _top = sorted(raw_text.values(), reverse=True)
    _stands_out = len(_top) >= 2 and _top[0] > 0 and _top[0] >= _top[1] * 1.3
    specific = _is_specific_query(query) or _stands_out
    ranked_ids = sorted(
        candidate_ids,
        key=lambda tid: score_tutor(
            similarity=similarity_map.get(tid, 0.0),
            average_rating=meta_map.get(tid, {}).get("average_rating"),
            total_reviews=meta_map.get(tid, {}).get("total_reviews"),
            completed_hours=meta_map.get(tid, {}).get("completed_hours"),
            specific_query=specific,
            text_rank=text_map.get(tid, 0.0),
        ),
        reverse=True,
    )[:top_k]

    return _build_results(ranked_ids, similarity_map, meta_map)
