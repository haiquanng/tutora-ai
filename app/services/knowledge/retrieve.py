"""
Truy hồi KB Tutora cho FAQ handler — tìm đoạn gần nghĩa nhất với câu hỏi.

Tách khỏi RAG môn học (shared/rag.py → match_rag_chunks trên rag_chunks). Ở đây dùng
match_tutora_kb trên bảng tutora_kb_chunks (nội dung Tutora, CEO nạp qua file).
"""
from __future__ import annotations

from ...core.config import get_settings
from ...core.dependencies import get_supabase, get_gemini_client

_settings = get_settings()

GEMINI_EMBED_MODEL = "gemini-embedding-2"
_DEFAULT_MIN_SIMILARITY = 0.6
_DEFAULT_TOP_K = 4


async def retrieve_kb(query: str, top_k: int = _DEFAULT_TOP_K,
                      min_similarity: float = _DEFAULT_MIN_SIMILARITY) -> list[dict]:
    """Trả list {content, title, similarity} đã lọc theo ngưỡng. Rỗng nếu không đủ gần /lỗi."""
    try:
        gemini = get_gemini_client()
        result = gemini.models.embed_content(
            model=GEMINI_EMBED_MODEL,
            contents=query,
            config={"output_dimensionality": _settings.rag_embedding_dim},
        )
        embedding = result.embeddings[0].values

        rows = get_supabase().rpc("match_tutora_kb", {
            "query_embedding": embedding,
            "match_count": top_k,
        }).execute().data or []
    except Exception as e:
        print(f"retrieve_kb error: {e}")
        return []

    return [r for r in rows if (r.get("similarity") or 0) >= min_similarity]
