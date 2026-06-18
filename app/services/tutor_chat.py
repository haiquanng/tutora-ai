"""
Tutor chat: guided wizard → hội thoại gợi ý gia sư.

Luồng (AI stateless — bên gọi giữ history, gửi kèm mỗi request):
  1. LLM (Gemini) đọc history + message + context → trích filter có cấu trúc
     (min_rate/max_rate/gender) + sinh câu trả lời ngắn. LLM "tối thiểu".
  2. Gọi .NET recommend (filter SQL + profile đầy đủ + vector rerank) với
     context + filter + query (message) → danh sách gia sư.
  3. Ghép { reply, tutors, filters, ai_ranked }.
"""
import json
import httpx
from google import genai
from google.genai import types

from ..core.config import get_settings
from ..core.dependencies import get_gemini_client
from ..models.schemas import (
    TutorChatRequest,
    TutorChatResponse,
    TutorChatFilters,
)

_settings = get_settings()

# Cache danh sách môn (id→tên) để map khi PH đổi môn giữa chat. Đổi rất hiếm.
_subjects_cache: list[dict] = []


async def _get_subjects() -> list[dict]:
    """Lấy [{subjectId, subjectName}] từ .NET; cache trong process."""
    global _subjects_cache
    if _subjects_cache:
        return _subjects_cache
    try:
        url = f"{_settings.dotnet_be_url}/api/subjects"
        async with httpx.AsyncClient(timeout=15) as client:
            r = await client.get(url, headers={"Accept": "application/json"})
            r.raise_for_status()
            data = r.json()
        _subjects_cache = data.get("content", data) or []
    except Exception as e:
        print(f"tutor_chat subjects fetch error: {e}")
    return _subjects_cache


_EXTRACT_PROMPT = """Bạn là trợ lý giúp phụ huynh tìm gia sư. Đọc hội thoại và TIN NHẮN MỚI NHẤT,
trả về JSON DUY NHẤT:
{{
  "action": "search" | "confirm_subject_change",
  "reply": "câu trả lời NGẮN (1-2 câu), thân thiện, tiếng Việt, BÁM SÁT tin nhắn mới nhất",
  "suggestions": ["gợi ý trả lời 1", "gợi ý 2", ...] (chỉ khi action=confirm_subject_change, tối đa 3),
  "filters": {{
    "min_rate": số VND/giờ hoặc null,
    "max_rate": số VND/giờ hoặc null,
    "tutor_gender": "male" | "female" | null,
    "subject_id": id môn (số) nếu phụ huynh muốn ĐỔI/THÊM môn, ngược lại null,
    "desired_count": số gia sư PH muốn xem nếu nêu rõ (vd "1-2 người" -> 2), ngược lại null
  }}
}}

DANH SÁCH MÔN (chọn đúng id khi đổi môn):
{subjects}

QUY TẮC:
- Chỉ điền field khi phụ huynh NÊU RÕ trong tin nhắn mới (vd "trên 150k" -> min_rate 150000;
  "dưới 200k" -> max_rate 200000; "giá cao quá" -> max_rate ~200000; "cô giáo" -> female;
  "cần 1-2 người" -> desired_count 2).

- ĐỔI/THÊM MÔN: Nếu PH muốn đổi sang môn KHÁC môn hiện tại "{current_subject}"
  (vd đang Ngữ văn, PH nói "tìm thêm gia sư Toán"):
  -> action = "confirm_subject_change", set subject_id = id môn mới.
  -> reply: HỎI LẠI xác nhận ngữ cảnh, KHÔNG khẳng định sẽ tìm ngay.
     Vd: "Dạ, bạn muốn tìm thêm gia sư Toán. Cho mình hỏi vẫn là bé đang học lớp như cũ,
     ôn thi như trước, hay là bé khác / mục tiêu khác ạ?"
  -> suggestions: 2-3 lựa chọn ngắn cho PH bấm, vd
     ["Vẫn bé đó, cùng mục tiêu", "Bé khác", "Mục tiêu khác"].
- Nếu KHÔNG đổi môn (giữ "{current_subject}") -> action = "search", subject_id = null,
  suggestions = [].
- Field nào tin mới không nhắc tới -> để null (hệ thống tự giữ giá trị cũ trong "Filter hiện tại").
- KHÔNG bịa, không chắc để null. KHÔNG lặp lại mô tả cũ trong reply.
- reply KHÔNG liệt kê tên gia sư (danh sách hiển thị riêng).
CHỈ trả JSON."""


async def _extract(
    gemini: genai.Client,
    history: list[dict],
    message: str,
    current: TutorChatFilters,
    subjects: list[dict],
    current_subject_id,
) -> dict:
    convo = "\n".join(f'{m["role"]}: {m["content"]}' for m in history) or "(chưa có)"
    subjects_text = "\n".join(f'- {s["subjectName"]}: id={s["subjectId"]}' for s in subjects) or "(không có)"
    cur_name = next((s["subjectName"] for s in subjects if s["subjectId"] == current_subject_id), str(current_subject_id))
    prompt = (
        _EXTRACT_PROMPT.format(subjects=subjects_text, current_subject=cur_name)
        + f"\n\nFilter hiện tại: {current.model_dump_json()}"
        + f"\n\nHội thoại trước:\n{convo}"
        + f"\n\nTin nhắn mới của phụ huynh: {message or '(chưa có, chỉ mới bắt đầu)'}"
    )
    try:
        resp = gemini.models.generate_content(
            model="gemini-2.5-flash-lite",
            contents=prompt,
            config=types.GenerateContentConfig(
                temperature=0.2,
                response_mime_type="application/json",
                thinking_config=types.ThinkingConfig(thinking_budget=0),
            ),
        )
        return json.loads(resp.text)
    except Exception as e:
        print(f"tutor_chat extract error: {e}")
        return {"reply": "", "filters": {}}


async def _fetch_candidates(context, filters: TutorChatFilters, query: str) -> dict:
    """Gọi .NET recommend (POST /api/tutors/recommend) — filter SQL + profile + rerank.
    subject_id từ filter (tích luỹ/đổi môn) ưu tiên hơn subject_id của wizard."""
    # desired_count nhỏ -> chỉ trả ít gia sư; mặc định 10 candidate.
    top_k = filters.desired_count if filters.desired_count and filters.desired_count > 0 else 10
    payload = {
        "subjectId": filters.subject_id or context.subject_id,
        "gradeLevelId": context.grade_level_id,
        "teachingMode": context.teaching_mode,
        "city": context.city,
        "minRate": filters.min_rate,
        "maxRate": filters.max_rate,
        "query": query or None,
        "topK": top_k,
    }
    url = f"{_settings.dotnet_be_url}/api/tutors/recommend"
    async with httpx.AsyncClient(timeout=30) as client:
        r = await client.post(url, json=payload)
        r.raise_for_status()
        data = r.json()
    # .NET bọc trong { content: {...} }
    return data.get("content", data)


def _merge_filters(prev: TutorChatFilters, new: TutorChatFilters) -> TutorChatFilters:
    """Tích luỹ state: giữ giá trị cũ, chỉ override field LLM vừa trích (non-null).
    desired_count KHÔNG dính: chỉ áp cho đúng turn PH nêu, turn sau về mặc định."""
    merged = prev.model_dump()
    for k, v in new.model_dump().items():
        if v is not None:
            merged[k] = v
    merged["desired_count"] = new.desired_count  # không kế thừa
    return TutorChatFilters(**merged)


async def tutor_chat(body: TutorChatRequest) -> TutorChatResponse:
    gemini = get_gemini_client()
    history = [m.model_dump() for m in body.history]
    subjects = await _get_subjects()

    # Filter đã tích luỹ (FE gửi kèm) làm base; subject hiện hành để LLM biết ngữ cảnh.
    prev = body.current_filters or TutorChatFilters()
    current_subject = prev.subject_id or body.context.subject_id

    # 1. LLM trích filter (gồm subject_id nếu đổi môn, desired_count) + reply
    extracted = await _extract(
        gemini, history, body.message, prev,
        subjects, current_subject,
    )
    new_filters = TutorChatFilters(**(extracted.get("filters") or {}))
    reply = (extracted.get("reply") or "").strip()
    action = extracted.get("action") or "search"

    # Đổi môn → HỎI LẠI xác nhận ngữ cảnh, CHƯA tìm gia sư turn này.
    # Reset filter của môn cũ (giá/query/gender), chỉ giữ môn mới + số lượng mong muốn;
    # lớp/hình thức thuộc context (về bé) nên không đụng. PH xác nhận xong mới tìm.
    if action == "confirm_subject_change" and new_filters.subject_id:
        pending = TutorChatFilters(
            subject_id=new_filters.subject_id,
            desired_count=new_filters.desired_count or prev.desired_count,
        )
        return TutorChatResponse(
            reply=reply or "Bạn muốn đổi môn ạ? Cho mình hỏi vẫn là bé như cũ hay bé khác nhé?",
            tutors=[], filters=pending, ai_ranked=False,
            awaiting_confirmation=True,
            suggestions=(extracted.get("suggestions") or [])[:3],
        )

    filters = _merge_filters(prev, new_filters)

    # 2. Gọi .NET lấy candidate đã filter + rerank
    try:
        content = await _fetch_candidates(body.context, filters, body.message)
        tutors = content.get("tutors", []) or []
        ai_ranked = bool(content.get("aiRanked", False))
        # Hạ gia sư chưa có đánh giá (totalReviews=0) xuống cuối — tránh người mới /
        # hồ sơ lệch bậc đứng top "Rất phù hợp". Stable: giữ thứ tự rerank trong từng nhóm.
        tutors.sort(key=lambda t: (t.get("totalReviews") or 0) == 0)
    except Exception as e:
        print(f"tutor_chat candidates error: {e}")
        return TutorChatResponse(
            reply="Xin lỗi, mình chưa tải được danh sách gia sư. Bạn thử lại giúp mình nhé.",
            tutors=[], filters=filters, ai_ranked=False,
        )

    # 3. Reply mặc định / cảnh báo rỗng — KHÔNG để PH hiểu nhầm danh sách cũ là kết quả.
    if not tutors:
        reply = (
            (reply + " ") if reply else ""
        ) + "Tiếc là chưa có gia sư nào khớp tiêu chí này. Bạn thử nới bớt yêu cầu (giá, môn…) nhé?"
    elif not reply:
        reply = f"Mình tìm được {len(tutors)} gia sư phù hợp:"

    return TutorChatResponse(reply=reply, tutors=tutors, filters=filters, ai_ranked=ai_ranked)
