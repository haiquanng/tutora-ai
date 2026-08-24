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
    """

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



async def retrieve_similar_for_practice(
    sb: Client,
    query: str,
    chapter: Optional[str] = None,
    difficulty: Optional[str] = None,
    exclude_ids: Optional[List[str]] = None,
    top_k: int = 5,
    gemini: Optional[genai.Client] = None,
    min_similarity: float = 0.60,
) -> List[dict]:
    """Câu để LUYỆN TẬP: tương tự về nội dung, KHÔNG phải trùng khít.

    Khác retrieve_questions (ngưỡng 0.88, dùng làm lời giải mẫu): ở đây ngưỡng thấp hơn
    hẳn vì mục tiêu là bài CÙNG DẠNG để rèn, trùng khít lại thành chép y nguyên.

    Lọc thêm difficulty khi có: học sinh vừa làm bài Nhận biết mà nhận ngay bài Vận dụng
    cao thì nản. match_questions không trả difficulty nên phải lấy bổ sung theo id.
    """
    try:
        if not gemini:
            return []
        embedding = gemini.models.embed_content(
            model="gemini-embedding-2",
            contents=query,
            config={"output_dimensionality": _settings.rag_embedding_dim},
        ).embeddings[0].values

        rows = sb.rpc("match_questions", {
            "query_embedding": embedding,
            # Lấy dư rồi mới lọc: loại câu đã làm + lọc độ khó sẽ cắt bớt kha khá.
            "match_count": max(top_k * 6, 30),
            "filter_subject_id": _SUBJECT_TOAN_ID,
            "filter_grade_id": None,
            "filter_chapter": None,
            "min_similarity": min_similarity,
        }).execute().data or []

        done = set(exclude_ids or [])
        rows = [
            r for r in rows
            if r.get("id") not in done
            and r.get("solution")
            and isinstance(r.get("similarity"), (int, float))
            and r["similarity"] == r["similarity"]          # loại NaN
            and (not chapter or r.get("chapter") == chapter)
        ]
        if not rows:
            return []

        # difficulty + review_status không có trong RPC -> tra bổ sung theo id.
        meta = sb.table("questions").select("id,difficulty,review_status") \
            .in_("id", [r["id"] for r in rows[:40]]).execute().data or []
        by_id = {m["id"]: m for m in meta}

        enriched = []
        for r in rows:
            m = by_id.get(r["id"])
            if not m or m.get("review_status") != "published":
                continue
            r["difficulty"] = m.get("difficulty")
            enriched.append(r)

        # Ưu tiên đúng độ khó; hết thì mới nới ra, thà lệch độ khó còn hơn không có bài.
        if difficulty:
            same = [r for r in enriched if r.get("difficulty") == difficulty]
            if same:
                enriched = same

        return enriched[:top_k]
    except Exception as e:
        logger.warning("retrieve_similar_for_practice thất bại: %s", e)
        return []
