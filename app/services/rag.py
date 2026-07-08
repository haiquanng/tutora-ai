from typing import Optional, List, Tuple
# from sentence_transformers import SentenceTransformer
from supabase import Client
from google import genai
from app.core.config import get_settings

_settings = get_settings()

# grade text (classifier/FE) -> grade_level_id (DB chinh). Question bank hien chi Toan.
_GRADE_TO_ID = {"9": 57, "10": 58, "11": 59, "12": 60, "thi_vao_10": 57}
_SUBJECT_TOAN_ID = 1


async def retrieve_questions(
    sb: Client,
    query: str,
    grade: Optional[str] = None,
    chapter: Optional[str] = None,
    top_k: int = 3,
    gemini: Optional[genai.Client] = None,
    min_similarity: float = 0.78,
) -> List[dict]:
    """Tim cau TUONG TU trong question bank (bang questions) da co loi giai mau.
    Tra ve list {content, solution, similarity, ...}. Rong neu khong trung / loi."""
    try:
        if not gemini:
            return []
        result = gemini.models.embed_content(
            model="gemini-embedding-2",
            contents=query,
            config={"output_dimensionality": _settings.rag_embedding_dim},
        )
        embedding = result.embeddings[0].values

        db_result = sb.rpc("match_questions", {
            "query_embedding": embedding,
            "match_count": top_k,
            "filter_subject_id": _SUBJECT_TOAN_ID,
            "filter_grade_id": _GRADE_TO_ID.get(grade),
            "filter_chapter": chapter,
            "min_similarity": min_similarity,
        }).execute()
        return db_result.data or []
    except Exception as e:
        print(f"match_questions error: {e}")
        return []

async def retrieve_chunks(
    sb: Client,
    model,  # unused — kept for interface compatibility
    query: str,
    grade: Optional[str] = None,
    chapter: Optional[str] = None,
    top_k: int = 3,
    gemini: Optional[genai.Client] = None,
    subject: str = "toan",
    min_similarity: float = 0.75,
) -> Tuple[List[dict], Optional[float]]:
    # min_similarity: ngưỡng lọc theo cosine. Đo thực tế trên gemini-embedding-2
    try:
        if not gemini:
            return [], None
        result = gemini.models.embed_content(
            model="gemini-embedding-2",
            contents=query,
            config={"output_dimensionality": _settings.rag_embedding_dim},
        )
        embedding = result.embeddings[0].values

        db_result = sb.rpc("match_rag_chunks", {
            "query_embedding": embedding,
            "match_count": top_k,
            "filter_grade": grade,
            "filter_chapter": chapter,
            "filter_subject": subject,
        }).execute()

        chunks = db_result.data or []
        filtered = [c for c in chunks if c.get("similarity", 0) >= min_similarity]
        if filtered:
            top_score = max(c.get("similarity", 0) for c in filtered)
            return filtered, top_score

        if chapter:
            fallback = sb.rpc("match_rag_chunks", {
                "query_embedding": embedding,
                "match_count": top_k,
                "filter_grade": grade,
                "filter_chapter": None,
                "filter_subject": subject,
            }).execute()
            fallback_filtered = [c for c in (fallback.data or []) if c.get("similarity", 0) >= min_similarity]
            if fallback_filtered:
                top_score = max(c.get("similarity", 0) for c in fallback_filtered)
                return fallback_filtered, top_score

        return [], None
    except Exception as e:
        print(f"RAG error: {e}")
        return [], None
