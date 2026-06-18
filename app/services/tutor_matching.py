from typing import Optional
from google import genai
from supabase import Client
from ..core.config import get_settings
from ..core.dependencies import get_supabase, get_gemini_client
from ..models.schemas import TutorRecommendResult

_settings = get_settings()

META_COLS = "tutor_id, city, district, teaching_mode, subject_ids, grades, price_min, price_max, average_rating, total_reviews, completed_hours"


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

    # --- No query: skip embedding, rank candidates by rating ---
    if not has_query:
        if not ids:
            return []
        fetch_ids = ids[:top_k]
        meta_rows = (
            sb.table("tutor_vectors")
            .select(META_COLS)
            .in_("tutor_id", fetch_ids)
            .execute()
            .data or []
        )
        # Sort by average_rating desc, preserve original order as tiebreak
        meta_map = {r["tutor_id"]: r for r in meta_rows}
        sorted_ids = sorted(
            fetch_ids,
            key=lambda tid: meta_map.get(tid, {}).get("average_rating") or 0,
            reverse=True,
        )
        return _build_results(sorted_ids, {}, meta_map)

    # --- Has query: embed and vector-rank ---
    gemini_client: genai.Client = get_gemini_client()
    result = gemini_client.models.embed_content(
        model="gemini-embedding-2",
        contents=query,
        config={"output_dimensionality": _settings.rag_embedding_dim},
    )
    embedding = result.embeddings[0].values

    rows = sb.rpc("match_tutors", {
        "query_embedding": embedding,
        "match_count": top_k,
        "filter_ids": ids,
    }).execute().data or []

    if not rows:
        return []

    tutor_ids = [r["tutor_id"] for r in rows]
    similarity_map = {r["tutor_id"]: r["similarity"] for r in rows}

    meta_rows = (
        sb.table("tutor_vectors")
        .select(META_COLS)
        .in_("tutor_id", tutor_ids)
        .execute()
        .data or []
    )
    meta_map = {r["tutor_id"]: r for r in meta_rows}

    return _build_results(tutor_ids, similarity_map, meta_map)
