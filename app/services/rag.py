from typing import Optional, List, Tuple
# from sentence_transformers import SentenceTransformer
from supabase import Client
from google import genai

async def retrieve_chunks(
    sb: Client,
    model,  # unused — kept for interface compatibility
    query: str,
    grade: Optional[str] = None,
    chapter: Optional[str] = None,
    top_k: int = 3,
    gemini: Optional[genai.Client] = None,
    subject: str = "toan",
) -> Tuple[List[dict], Optional[float]]:
    try:
        if not gemini:
            return [], None
        result = gemini.models.embed_content(
            model="gemini-embedding-2",
            contents=query,
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
        filtered = [c for c in chunks if c.get("similarity", 0) >= 0.75]
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
            fallback_filtered = [c for c in (fallback.data or []) if c.get("similarity", 0) >= 0.75]
            if fallback_filtered:
                top_score = max(c.get("similarity", 0) for c in fallback_filtered)
                return fallback_filtered, top_score

        return [], None
    except Exception as e:
        print(f"RAG error: {e}")
        return [], None
