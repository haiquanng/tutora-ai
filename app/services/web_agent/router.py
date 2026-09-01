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
import re

from google import genai
from google.genai import types

from ...core.dependencies import get_gemini_client
from ...models.schemas import TutorChatFilters
from ..telemetry.usage import track

_MODEL = "gemini-2.5-flash-lite"


_ROUTER_PROMPT = """Bạn là bộ ĐỊNH TUYẾN cho trợ lý AI của Tutora — nền tảng kết nối phụ
huynh/học sinh với GIA SƯ. Đọc lịch sử hội thoại + TIN NHẮN MỚI NHẤT, trả về JSON DUY NHẤT
(không markdown, không giải thích):
{{
  "scope": "on_topic" | "off_topic",
  "intent": "tutor" | "tutor_info" | "consult" | "faq" | "chitchat",
  "reply": "câu trả lời NGẮN 1-2 câu, thân thiện, tiếng Việt, bám sát tin nhắn mới",
  "suggestions": ["gợi ý bấm tiếp 1", "gợi ý 2"] (tối đa 3, [] nếu không cần),
  "filters": {{
    "min_rate": số VND/giờ hoặc null,
    "max_rate": số VND/giờ hoặc null,
    "tutor_gender": "male" | "female" | null,
    "subject_id": id môn (số) nếu user muốn tìm/đổi môn, ngược lại null,
    "grade_level_id": id LỚP (số) nếu user nêu lớp/cấp học, ngược lại null,
    "desired_count": số gia sư user muốn xem nếu nêu rõ, ngược lại null,
    "available_days": [số thứ] nếu user nêu NGÀY học mong muốn, ngược lại null,
    "available_from": "HH:MM" nếu user nêu KHUNG GIỜ, ngược lại null,
    "available_to": "HH:MM" nếu user nêu KHUNG GIỜ, ngược lại null
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
- "consult": user muốn tìm gia sư nhưng bạn còn THIẾU thông tin THẬT SỰ cần để tìm đúng.
  Chỉ dùng khi thiếu MÔN hoặc thiếu LỚP. reply = hỏi ĐÚNG cái còn thiếu, gộp tự nhiên
  trong 1 câu, KHÔNG hỏi dồn dập, KHÔNG hỏi lại cái user đã nói.
  VÍ DỤ: "Tìm gia sư Toán" (thiếu lớp) → consult: "Bé học lớp mấy để mình chọn đúng ạ?"
- "tutor": ĐÃ có MÔN + LỚP → giới thiệu gia sư NGAY. Không cần biết ngân sách/mục tiêu mới
  được tìm; những cái đó là TÙY CHỌN, hỏi thêm SAU khi đã cho xem kết quả cũng được.
- "tutor_info": hỏi VỀ MỘT GIA SƯ ĐÃ ĐƯỢC GỢI Ý ở lượt trước ("thầy A dạy sao?", "cô kia
  có kinh nghiệm không?", "người đầu tiên học phí bao nhiêu?", "bằng cấp của cô ấy?").
  Dấu hiệu: nhắc TÊN hoặc thứ tự của gia sư trong DANH SÁCH ĐÃ GỢI Ý bên dưới, và đang HỎI
  THÔNG TIN chứ không xin danh sách mới. KHÔNG dùng khi user muốn tìm/lọc gia sư khác.
- "faq": hỏi về HỆ THỐNG/CHÍNH SÁCH Tutora (học phí chung, cách đăng ký, quy trình, hoàn
  tiền, cam kết, an toàn, cách Tutora hoạt động) — KHÔNG nhằm tìm 1 gia sư cụ thể.

QUAN TRỌNG — HÀNH XỬ NHƯ TRỢ LÝ NGƯỜI THẬT, KHÔNG HỎI THỦ TỤC:
- Nguyên tắc: hỏi cái mình CHƯA BIẾT, không hỏi cho đủ quy trình. Người thật đã nghe đủ
  thì bắt tay vào làm ngay, rồi tinh chỉnh sau — không "phỏng vấn" thêm 2 lượt.
- ĐỌC KỸ TOÀN BỘ lịch sử + tin nhắn mới. Mọi thông tin user ĐÃ nói (kể cả nói ở lượt đầu)
  đều coi là ĐÃ BIẾT. Đủ môn + lớp là ĐỦ để chuyển "tutor" — kể cả ngay lượt ĐẦU TIÊN.
  VÍ DỤ: "tìm gia sư toán 12 có bằng thạc sĩ" → "tutor" NGAY (có môn + lớp), TUYỆT ĐỐI
  không hỏi lại ngân sách/kinh nghiệm trước khi cho xem gia sư.
- TUYỆT ĐỐI KHÔNG hỏi lại thông tin user đã cung cấp. Hỏi lại điều đã nói là lỗi nặng,
  gây cảm giác không được lắng nghe.
- Muốn lọc kỹ hơn thì cho xem kết quả TRƯỚC, rồi mời tinh chỉnh: "Đây là vài gia sư phù
  hợp. Nếu bạn cho mình biết thêm ngân sách, mình lọc sát hơn nhé."
- suggestions: gợi ý BƯỚC TIẾP THEO, KHÔNG được lặp lại nội dung user vừa nói. Nếu user
  vừa nói "Toán 12 có bằng thạc sĩ" thì đừng gợi ý "Tìm gia sư Toán 12 có bằng thạc sĩ".
- NGƯỢC LẠI, KHÔNG được "vờ như đã hiểu": nếu tin nhắn KHÔNG PHẢI yêu cầu tìm gia sư rõ
  ràng (user dán 1 đoạn văn/mô tả dài, câu tối nghĩa, chỉ vài từ rời rạc, hoặc không rõ
  muốn gì) → intent "consult", reply HỎI LẠI cho rõ. TUYỆT ĐỐI KHÔNG tóm tắt lại tiêu chí
  của lượt trước rồi nói "mình sẽ tìm..." như thể đã hiểu tin nhắn mới — đó là bịa.
  VÍ DỤ: user dán đoạn giới thiệu của 1 gia sư → consult: "Bạn muốn tìm gia sư có đặc điểm
  giống mô tả này phải không ạ? Cho mình biết môn và lớp cần học nhé."
- Đừng hỏi những câu máy móc vô nghĩa như "bạn muốn tìm bao nhiêu gia sư?".

LỊCH RẢNH (available_days / available_from / available_to) — quy đổi sang SỐ và GIỜ:
- Thứ (CHÚ Ý: mã KHÁC con số trong tên thứ, đừng lấy nguyên số user gõ):
    "thứ 2"/"thứ hai"  → 1        "thứ 5"/"thứ năm" → 4
    "thứ 3"/"thứ ba"   → 2        "thứ 6"/"thứ sáu" → 5
    "thứ 4"/"thứ tư"   → 3        "thứ 7"/"thứ bảy" → 6   ← KHÔNG phải 7
    "chủ nhật"/"CN"    → 7
  Quy tắc: mã = (số trong tên thứ) − 1; riêng Chủ Nhật = 7.
  "cuối tuần" → [6,7]; "trong tuần"/"ngày thường" → [1,2,3,4,5]; "thứ 3 và thứ 5" → [2,4].
- Khung giờ: "sáng" → 08:00-11:00; "chiều" → 14:00-17:00; "tối" → 18:00-21:00.
  Giờ cụ thể thì lấy đúng ("sau 19h" → 19:00-21:00; "7-9h tối" → 19:00-21:00).
- CHỈ điền khi user nêu; không nhắc → null. Đây là filter CỨNG (lọc thẳng ở DB), nên
  reply ĐƯỢC PHÉP khẳng định đã lọc theo lịch (khác bằng cấp — cái đó chỉ ưu tiên).

DANH SÁCH MÔN (chọn đúng id khi user nêu môn):
{subjects}

DANH SÁCH LỚP (chọn đúng id khi user nêu lớp — "lớp 9" là gradeLevelId chứ KHÔNG phải số 9):
{grades}

QUY TẮC:
- CHỈ điền filter khi user NÊU RÕ ("dưới 200k" → max_rate 200000; "cô giáo" → female;
  "lớp 9" → grade_level_id đúng id trong danh sách; "cần 2 người" → desired_count 2).
  Không nhắc → null (hệ thống giữ giá trị cũ).
- User BỎ 1 tiêu chí ("bỏ giới hạn giá", "giá nào cũng được", "không cần cô giáo nữa")
  → điền "__clear__" cho field đó (KHÁC null: null = giữ nguyên, "__clear__" = xoá).
- ĐỔI MÔN: user chuyển sang môn khác ("giờ tìm gia sư Tiếng Anh") → điền subject_id môn
  mới, VÀ "__clear__" cho các tiêu chí gắn với môn cũ mà user không nhắc lại (min_rate,
  max_rate) — giá của môn cũ không được dính sang môn mới.
- reply KHÔNG liệt kê tên gia sư (card hiển thị riêng). KHÔNG bịa. Ngắn gọn.
- TUYỆT ĐỐI KHÔNG khẳng định đã lọc theo tiêu chí mà filter KHÔNG có field tương ứng.
  Hệ thống chỉ lọc được: giá, giới tính, môn, lớp, số lượng. Các mong muốn khác (bằng cấp/
  thạc sĩ, kinh nghiệm, tính cách, trường...) chỉ dùng để XẾP HẠNG, KHÔNG phải lọc cứng →
  reply nói "mình sẽ ưu tiên gia sư có...", KHÔNG được nói "gia sư có bằng Thạc sĩ" như
  thể đã lọc chắc chắn (nói vậy là SAI SỰ THẬT, mất niềm tin).
- Filter hiện tại (đã tích luỹ): {current_filters}
CHỈ trả JSON."""


async def route(
    history: list[dict],
    message: str,
    current_filters: TutorChatFilters,
    subjects: list[dict],
    grades: list[dict],
    shown_tutors: list | None = None,
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
    shown_text = "\n".join(
        f'- {getattr(t, "name", "") or getattr(t, "tutor_id", "")}'
        for t in (shown_tutors or [])
    ) or "(chưa gợi ý gia sư nào)"
    prompt = (
        _ROUTER_PROMPT.format(
            subjects=subjects_text, grades=grades_text,
            current_filters=current_filters.model_dump_json(),
        )
        + f"\n\nDANH SÁCH GIA SƯ ĐÃ GỢI Ý (dùng để nhận diện intent 'tutor_info'):\n{shown_text}"
        + f"\n\nHội thoại trước:\n{convo}"
        + f"\n\nTin nhắn mới: {message or '(chưa có, mới bắt đầu)'}"
    )
    try:
        with track("web_agent_router", _MODEL) as _t:
            resp = _t.done(gemini.models.generate_content(
                model=_MODEL,
                contents=prompt,
                config=types.GenerateContentConfig(
                    temperature=0.1,
                    response_mime_type="application/json",
                    thinking_config=types.ThinkingConfig(thinking_budget=0),
                ),
            ))
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
    if intent not in ("tutor", "tutor_info", "consult", "faq", "chitchat"):
        intent = "chitchat"
    return {
        "scope": scope,
        "intent": intent,
        "reply": (data.get("reply") or "").strip(),
        "suggestions": (data.get("suggestions") or [])[:3],
        "filters": _fix_grade(_fix_days(data.get("filters") or {}, message), message, grades),
    }


# Thứ trong tin nhắn → mã days_of_week.
_DAY_WORDS = [
    (("chủ nhật", "chu nhat", "cn"), 7),
    (("thứ hai", "thu hai", "thứ 2", "thu 2", "t2"), 1),
    (("thứ ba", "thu ba", "thứ 3", "thu 3", "t3"), 2),
    (("thứ tư", "thu tu", "thứ 4", "thu 4", "t4"), 3),
    (("thứ năm", "thu nam", "thứ 5", "thu 5", "t5"), 4),
    (("thứ sáu", "thu sau", "thứ 6", "thu 6", "t6"), 5),
    (("thứ bảy", "thu bay", "thứ 7", "thu 7", "t7"), 6),
]


_GRADE_RE = re.compile(
    r"(?:lớp|lop|khối|khoi|grade)\s*(\d{1,2})"      # "lớp 11"
    r"|(?<![\d,.])(\d{1,2})\s*(?:tuổi|tuoi)"        # "11 tuổi" → không phải lớp, loại sau
    r"|^\s*(\d{1,2})\s*(?:[,.]|$|\s)",              # "11" hoặc "11, và..." đứng đầu câu
    re.IGNORECASE,
)


def _fix_grade(filters: dict, message: str, grades: list[dict]) -> dict:
    """Chốt grade_level_id từ tin nhắn gốc khi user nêu lớp tường minh.
    """
    if not message or not grades:
        return filters
    m = _GRADE_RE.search(message.strip().lower())
    if not m:
        return filters
    # Nhóm 2 là "N tuổi" — tuổi KHÁC lớp, không được suy ra.
    if m.group(2):
        return filters
    num = m.group(1) or m.group(3)
    if not num:
        return filters
    want = f"lớp {int(num)}"
    for g in grades:
        if (g.get("gradeName") or "").strip().lower() == want:
            filters["grade_level_id"] = g.get("gradeLevelId")
            break
    return filters


def _fix_days(filters: dict, message: str) -> dict:
    """Sửa lại available_days theo tin nhắn gốc khi user gọi tên thứ tường minh.

    Chỉ can thiệp khi câu có nêu thứ cụ thể — "cuối tuần"/"trong tuần" vẫn để LLM suy luận.
    """
    if not message or "available_days" not in filters:
        return filters
    msg = message.lower()
    found = sorted({code for words, code in _DAY_WORDS if any(w in msg for w in words)})
    if found and filters.get("available_days") != found:
        filters["available_days"] = found
    return filters
