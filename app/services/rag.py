from typing import Optional, List
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
) -> List[dict]:
    try:
        # embedding = model.encode(query).tolist()

        # --- Gemini embedding API
        if not gemini:
            return []
        result = gemini.models.embed_content(
            model="gemini-embedding-2",
            contents=query,
        )
        embedding = result.embeddings[0].values

        db_result = sb.rpc("match_rag_chunks", {
            "query_embedding": embedding,
            "match_count": top_k,
            "filter_grade": grade,
            "filter_chapter": chapter
        }).execute()
        return db_result.data or []
    except Exception as e:
        print(f"RAG error: {e}")
        return []
