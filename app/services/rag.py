from typing import Optional, List
from sentence_transformers import SentenceTransformer
from supabase import Client

async def retrieve_chunks(
    sb: Client,
    model: SentenceTransformer,
    query: str,
    grade: Optional[str] = None,
    chapter: Optional[str] = None,
    top_k: int = 3
) -> List[dict]:
    """Vector search với filter grade/chapter."""
    try:
        embedding = model.encode(query).tolist()
        result = sb.rpc("match_rag_chunks", {
            "query_embedding": embedding,
            "match_count": top_k,
            "filter_grade": grade,
            "filter_chapter": chapter
        }).execute()
        return result.data or []
    except Exception as e:
        print(f"RAG error: {e}")
        return []
