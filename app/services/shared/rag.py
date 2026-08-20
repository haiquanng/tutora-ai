import logging
from typing import Optional, List
from supabase import Client
from google import genai
from app.core.config import get_settings

logger = logging.getLogger(__name__)
_settings = get_settings()

# Question bank hiện chỉ có Toán.
_SUBJECT_TOAN_ID = 1


async def retrieve_questions(
    sb: Client,
    query: str,
    grade: Optional[str] = None,
    chapter: Optional[str] = None,
    top_k: int = 3,
    gemini: Optional[genai.Client] = None,
    min_similarity: float = 0.88,
) -> List[dict]:
    """Tìm câu tương tự trong question bank (questions.embedding, đã duyệt published).

    Trả list {id, content, solution, similarity, chapter, ...}, rỗng nếu không trúng.

    KHÔNG lọc theo grade lẫn chapter — cả hai đều là điều kiện CỨNG dựa trên nhãn
    không đáng tin, và trượt nhãn thì mất trắng bài khớp:
      - chapter: bank có 2 slug cho cùng một chương ('cap_so_cong' và
        'day_so_cap_so_cong_cap_so_nhan').
      - grade: classifier đoán "chia đa thức có dư" là lớp 10, bank xếp lớp 9 ->
        lọc theo lớp cho 0 kết quả trong khi bài khớp similarity 1.0000 nằm ngay đó.
        Lớp vẫn dùng cho prompt (ràng buộc công cụ), chỉ KHÔNG dùng để lọc bank.

    Ngưỡng 0.88 thay ngưỡng cũ 0.78 mới là thứ chặn bài lạc đề: 0.78 từng nối "chia đa
    thức có dư" (lớp 9) với "đồ thị hàm bậc ba qua 4 điểm" (lớp 12) chỉ vì cùng chứa
    ax^3+bx^2+cx. Lời giải mẫu lạc đề hại hơn không có, vì model bám theo nó."""
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
            "filter_grade_id": None,
            "filter_chapter": None,
            "min_similarity": min_similarity,
        }).execute()
        rows = db_result.data or []
        # similarity=NaN khi vector truy vấn suy biến -> loại, tránh lọt vào nhãn tin cậy.
        return [r for r in rows if isinstance(r.get("similarity"), (int, float))
                and r["similarity"] == r["similarity"]]
    except Exception as e:
        logger.warning("match_questions thất bại (grade=%s): %s", grade, e)
        return []

