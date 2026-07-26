"""
Agent router (pattern Kodee: agent_router phân loại rồi chọn handler).

1 LLM call JSON DUY NHẤT quyết cả 3 việc — tiết kiệm latency/chi phí so với Kodee tách
riêng handoff_classifier + agent_router + extract (họ scale rất lớn nên tách; ta gộp,
vẫn deterministic vì CODE quyết route dựa JSON, LLM chỉ phân loại + trích, không tự hành động):

  scope   : "on_topic" | "off_topic"          (guardrail — câu có liên quan Tutora không)
  intent  : "tutor" | "faq" | "chitchat"      (chọn handler khi on_topic)
  reply   : câu trả lời ngắn (chào/dẫn dắt/hỏi lại) — handler có thể ghi đè
  filters : min_rate/max_rate/gender/subject_id/desired_count (cho handler tutor)

Temperature thấp (0.1) — phân loại phải TẤT ĐỊNH, không sáng tạo (bài học temperature
của Kodee: gần 0 cho câu factual/phân loại).
"""
from __future__ import annotations

import json

from google import genai
from google.genai import types

from ...core.dependencies import get_gemini_client
from ...models.schemas import TutorChatFilters

_MODEL = "gemini-2.5-flash-lite"


_ROUTER_PROMPT = """Bạn là bộ ĐỊNH TUYẾN cho trợ lý AI của Tutora — nền tảng kết nối phụ
huynh/học sinh với GIA SƯ. Đọc lịch sử hội thoại + TIN NHẮN MỚI NHẤT, trả về JSON DUY NHẤT
(không markdown, không giải thích):
{{
  "scope": "on_topic" | "off_topic",
  "intent": "tutor" | "consult" | "faq" | "chitchat",
  "reply": "câu trả lời NGẮN 1-2 câu, thân thiện, tiếng Việt, bám sát tin nhắn mới",
  "suggestions": ["gợi ý bấm tiếp 1", "gợi ý 2"] (tối đa 3, [] nếu không cần),
  "filters": {{
    "min_rate": số VND/giờ hoặc null,
    "max_rate": số VND/giờ hoặc null,
    "tutor_gender": "male" | "female" | null,
    "subject_id": id môn (số) nếu user muốn tìm/đổi môn, ngược lại null,
    "grade_level_id": id LỚP (số) nếu user nêu lớp/cấp học, ngược lại null,
    "desired_count": số gia sư user muốn xem nếu nêu rõ, ngược lại null
  }}
}}

PHẠM VI (scope):
- "on_topic": mọi câu liên quan tìm gia sư, học tập, hoặc hệ thống/chính sách Tutora
  (học phí, cách hoạt động, đăng ký, hoàn tiền, an toàn, cách chọn gia sư...).
- "off_topic": câu KHÔNG liên quan Tutora/gia sư/học tập (thời tiết, thể thao, code hộ,
  chính trị, tán gẫu vô thưởng vô phạt, hỏi kiến thức chung không nhằm tìm gia sư...).
  → intent bất kỳ, reply = câu TỪ CHỐI lịch sự, nhắc mình chỉ hỗ trợ về gia sư/Tutora.

INTENT (chỉ xét khi on_topic):
- "chitchat": CHÀO HỎI, mở đầu, cảm ơn, câu CHƯA nêu nhu cầu gì ("hello", "chào",
  "bạn giúp được gì"). reply DẪN DẮT hỏi cần môn gì.
- "consult": user MUỐN tìm gia sư nhưng CHƯA ĐỦ thông tin để tư vấn đúng. Đây là trạng thái
  MẶC ĐỊNH khi mới nêu nhu cầu — PHẢI tư vấn hỏi thêm TRƯỚC khi giới thiệu gia sư, KHÔNG
  bắn gia sư sớm chỉ với môn. Cần đủ CẢ HAI trước khi chuyển "tutor":
    (1) LỚP/cấp học (lớp mấy, hoặc luyện thi gì: SAT/IELTS/đại học...), VÀ
    (2) đã hỏi (dù chỉ 1 lần) về NHU CẦU: mục tiêu học (mất gốc/củng cố/nâng cao/ôn thi),
        ngân sách, hoặc mong muốn về gia sư (cách dạy, tính cách, giới tính, online/tại nhà).
  reply = câu HỎI để lấp thông tin còn thiếu (hỏi gộp tự nhiên, đừng hỏi dồn dập từng cái).
  VÍ DỤ: "Tìm gia sư Toán" → consult, hỏi "Bé học lớp mấy, và đang cần củng cố hay nâng cao,
  ngân sách khoảng bao nhiêu để mình chọn gia sư phù hợp nhất ạ?"
- "tutor": ĐÃ đủ (1)+(2) (đọc lịch sử: user đã cho biết lớp VÀ mình đã hỏi nhu cầu ít nhất
  1 lần — dù user trả lời hay từ chối nói thêm). Giờ mới giới thiệu gia sư.
- "faq": hỏi về HỆ THỐNG/CHÍNH SÁCH Tutora (học phí chung, cách đăng ký, quy trình, hoàn
  tiền, cam kết, an toàn, cách Tutora hoạt động) — KHÔNG nhằm tìm 1 gia sư cụ thể.

QUAN TRỌNG — chống hỏi lặp: ĐỌC KỸ lịch sử. Nếu mình ĐÃ hỏi nhu cầu ở lượt trước và user vừa
trả lời (hoặc nói "không cần thêm", "gợi ý luôn đi"), coi (2) ĐÃ XONG → chuyển "tutor", đừng
hỏi lại. Ngược lại chỉ mới có môn (chưa có lớp / chưa hỏi nhu cầu) → "consult".

DANH SÁCH MÔN (chọn đúng id khi user nêu môn):
{subjects}

DANH SÁCH LỚP (chọn đúng id khi user nêu lớp — "lớp 9" là gradeLevelId chứ KHÔNG phải số 9):
{grades}

QUY TẮC:
- CHỈ điền filter khi user NÊU RÕ ("dưới 200k" → max_rate 200000; "cô giáo" → female;
  "lớp 9" → grade_level_id đúng id trong danh sách; "cần 2 người" → desired_count 2).
  Không nhắc → null (hệ thống giữ giá trị cũ).
- reply KHÔNG liệt kê tên gia sư (card hiển thị riêng). KHÔNG bịa. Ngắn gọn.
- Filter hiện tại (đã tích luỹ): {current_filters}
CHỈ trả JSON."""


async def route(
    history: list[dict],
    message: str,
    current_filters: TutorChatFilters,
    subjects: list[dict],
    grades: list[dict],
) -> dict:
    """1 call → {scope, intent, reply, suggestions, filters}. Fallback an toàn nếu lỗi:
    coi như on_topic/tutor để không chặn nhầm nhu cầu thật."""
    gemini: genai.Client = get_gemini_client()
    convo = "\n".join(f'{m["role"]}: {m["content"]}' for m in history) or "(chưa có)"
    subjects_text = "\n".join(
        f'- {s["subjectName"]}: id={s["subjectId"]}' for s in subjects
    ) or "(không có)"
    grades_text = "\n".join(
        f'- {g["gradeName"]}: id={g["gradeLevelId"]}' for g in grades
    ) or "(không có)"
    prompt = (
        _ROUTER_PROMPT.format(
            subjects=subjects_text, grades=grades_text,
            current_filters=current_filters.model_dump_json(),
        )
        + f"\n\nHội thoại trước:\n{convo}"
        + f"\n\nTin nhắn mới: {message or '(chưa có, mới bắt đầu)'}"
    )
    try:
        resp = gemini.models.generate_content(
            model=_MODEL,
            contents=prompt,
            config=types.GenerateContentConfig(
                temperature=0.1,
                response_mime_type="application/json",
                thinking_config=types.ThinkingConfig(thinking_budget=0),
            ),
        )
        data = json.loads(resp.text)
    except Exception as e:
        print(f"web router error: {e}")
        # Fallback an toàn: chitchat (dẫn dắt hỏi nhu cầu), KHÔNG tutor — tránh gọi .NET
        # recommend khi chưa có tiêu chí và trả câu "chưa tải được gia sư" khó hiểu.
        return {"scope": "on_topic", "intent": "chitchat", "reply": "", "suggestions": [], "filters": {}}

    scope = data.get("scope")
    if scope not in ("on_topic", "off_topic"):
        scope = "on_topic"
    intent = data.get("intent")
    if intent not in ("tutor", "consult", "faq", "chitchat"):
        intent = "chitchat"
    return {
        "scope": scope,
        "intent": intent,
        "reply": (data.get("reply") or "").strip(),
        "suggestions": (data.get("suggestions") or [])[:3],
        "filters": data.get("filters") or {},
    }
