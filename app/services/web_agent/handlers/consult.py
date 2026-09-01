"""
Consult handler — chưa đủ thông tin để tư vấn đúng, hỏi thêm rồi mới giới thiệu gia sư.

Hai trường hợp:
  1. Thiếu MÔN hoặc LỚP  → hỏi cái còn thiếu.
  2. Đã có môn + lớp nhưng CHƯA có tiêu chí nào khác → hỏi ĐÚNG 1 câu gợi mở về giá /
     lịch học / giới tính. Chỉ 2 thông tin thì kết quả còn quá chung, tư vấn chưa có giá trị.

Câu hỏi do LLM diễn đạt để tự nhiên và đổi theo ngữ cảnh, thay vì chuỗi cứng lặp y hệt mỗi lượt.
"""
from __future__ import annotations

from google.genai import types

from ..schemas import WebChatResponse
from ..handlers.base import BaseHandler, HandlerContext
from ....core.dependencies import get_gemini_client
from ...telemetry.usage import track

_MODEL = "gemini-2.5-flash-lite"

ASKED_MARK = "\u200b"

_DEFAULT_REPLY = (
    "Để mình gợi ý sát hơn, bạn cho mình biết thêm mức học phí mong muốn, "
    "hoặc bé học được vào khung giờ nào ạ?"
)


def _ask_more(ctx: HandlerContext) -> str:
    """1 câu hỏi thêm về tiêu chí user CHƯA nêu. Code chọn nội dung, LLM diễn đạt."""
    missing = []
    if not (ctx.filters.min_rate or ctx.filters.max_rate):
        missing.append("mức học phí mong muốn")
    if not getattr(ctx.filters, "available_days", None):
        missing.append("buổi/khung giờ học được")
    if not ctx.filters.tutor_gender:
        missing.append("muốn thầy hay cô")
    if not missing:
        return ""

    prompt = (
        "Bạn là trợ lý Tutora. Phụ huynh/học sinh đã cho biết môn và lớp cần học, nhưng "
        "chưa nêu tiêu chí nào khác nên mình chưa lọc sát được.\n\n"
        "Viết ĐÚNG MỘT câu ngắn (tối đa 30 từ), thân thiện, tiếng Việt, xưng 'mình', hỏi "
        "thêm về MỘT-HAI tiêu chí trong danh sách sau để tìm gia sư phù hợp hơn:\n"
        + "\n".join(f"- {m}" for m in missing) +
        "\n\nYÊU CẦU:\n"
        "- Chỉ 1 câu hỏi, KHÔNG liệt kê hết mọi tiêu chí.\n"
        "- Giọng nhẹ nhàng, cho họ thoải mái nói 'không có yêu cầu gì' cũng được.\n"
        "- KHÔNG hỏi lại môn hay lớp (đã biết rồi).\n"
        "- KHÔNG chào lại, KHÔNG nhắc tên gia sư. Chỉ trả về câu đó."
    )
    try:
        with track("web_agent_consult", _MODEL) as _t:
            resp = _t.done(get_gemini_client().models.generate_content(
                model=_MODEL, contents=prompt,
                config=types.GenerateContentConfig(
                    temperature=0.7,   # cần đa dạng, không lặp y hệt mỗi lượt
                    thinking_config=types.ThinkingConfig(thinking_budget=0),
                ),
            ))
        return (resp.text or "").strip().strip('"')
    except Exception as e:
        print(f"web consult ask-more error: {e}")
        return ""


class ConsultHandler(BaseHandler):
    label = "consult"

    async def handle(self, ctx: HandlerContext) -> WebChatResponse:
        has_subject = bool(ctx.filters.subject_id or ctx.context.subject_id)
        has_grade = bool(ctx.filters.grade_level_id or ctx.context.grade_level_id)

        if has_subject and has_grade:
            # Đủ môn+lớp nhưng thiếu tiêu chí khác → hỏi thêm 1 câu
            reply = _ask_more(ctx) or _DEFAULT_REPLY
            return WebChatResponse(
                reply=reply + ASKED_MARK, intent="consult",
                filters=ctx.filters, suggestions=ctx.suggestions,
            )

        # Thiếu môn/lớp → dùng câu router đã sinh (nó biết đang thiếu cái gì).
        return WebChatResponse(
            reply=ctx.router_reply or "Bạn muốn tìm gia sư môn gì, cho bé lớp mấy ạ?",
            intent="consult", filters=ctx.filters, suggestions=ctx.suggestions,
        )
