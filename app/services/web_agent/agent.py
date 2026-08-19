"""
Web agent — điều phối chatbot tự do 3 mục đích cho Tutora-FE (thay tutor_chat.py cũ).

Kiến trúc theo pattern Kodee (guardrail → router → specialized handler), 

Luồng 1 lượt:
  1. Lấy subjects (.NET) + merge filter tích luỹ (FE gửi) với filter mới router trích.
  2. router.route() → {scope, intent, reply, suggestions, filters}.
  3. scope='off_topic' → guardrail từ chối lịch sự, DỪNG (không search/RAG).
  4. intent='tutor' → TutorHandler (function .NET recommend → card).
     intent='faq'   → FaqHandler (RAG KB Tutora, chống bịa).
"""
from __future__ import annotations

from .schemas import WebChatRequest, WebChatResponse
from . import router as router_mod
from . import guardrail
from .handlers.base import HandlerContext
from .handlers.tutor import TutorHandler
from .handlers.faq import FaqHandler
from .handlers.chitchat import ChitchatHandler
from .handlers.consult import ConsultHandler
from ..tutoring_shared.candidates import _get_subjects, _get_grades
from ...models.schemas import TutorChatFilters

# Đăng ký handler theo intent (Kodee: agent_router map label → handler). Thêm handler mới
# (booking, gói học...) chỉ cần thêm 1 dòng ở đây + 1 file trong handlers/.
_HANDLERS = {
    "tutor": TutorHandler(),
    "consult": ConsultHandler(),
    "faq": FaqHandler(),
    "chitchat": ChitchatHandler(),
}


# Sentinel router dùng để XOÁ 1 filter (khác null = "không nhắc, giữ nguyên").
_CLEAR = "__clear__"

# Ngưỡng coi tin nhắn là "đoạn văn dán vào" chứ không phải câu yêu cầu. Câu tìm gia sư thật
# hiếm khi dài quá mức này ("mình cần gia sư Toán lớp 12, ưu tiên thạc sĩ, dưới 300k" ~70 ký tự).
_LONG_MESSAGE_CHARS = 220


def _merge_filters(prev: TutorChatFilters, new: dict) -> TutorChatFilters:
    """Tích luỹ state: giữ giá trị cũ, chỉ override field router vừa trích (non-null).

    Sentinel "__clear__": user BỎ một tiêu chí ("bỏ giới hạn giá", "môn nào cũng được")
    — null nghĩa là "không nhắc, giữ nguyên", nên cần đường riêng để XOÁ, không thì filter
    dính vĩnh viễn (bug: đổi sang Tiếng Anh vẫn còn max_rate của lượt hỏi Toán).
    """
    merged = prev.model_dump()
    for k, v in new.items():
        if k not in merged:
            continue
        if v == _CLEAR:
            merged[k] = None
        elif v is not None:
            merged[k] = v
    return TutorChatFilters(**merged)


async def web_chat(body: WebChatRequest) -> WebChatResponse:
    history = [m.model_dump() for m in body.history]
    subjects = await _get_subjects()
    grades = await _get_grades()
    prev = body.current_filters or TutorChatFilters()

    # (2) Router: 1 call phân scope + intent + trích filter (gồm lớp) + reply.
    routed = await router_mod.route(history, body.message, prev, subjects, grades)

    # (3) Guardrail: off-topic → từ chối, dừng ngay.
    if guardrail.is_off_topic(routed["scope"]):
        return WebChatResponse(
            reply=guardrail.refusal_reply(routed["reply"]),
            intent="off_topic",
            suggestions=routed["suggestions"],
        )

    filters = _merge_filters(prev, routed["filters"])

    # (3b) Chống "hỏi thủ tục": ĐỦ môn + lớp là đủ để tìm → ép sang tutor dù LLM còn muốn
    # hỏi thêm. Prompt một mình không đủ tin cậy (LLM hay hỏi lại cho "đúng quy trình"),
    # mà hỏi lại điều user ĐÃ nói là lỗi nặng nhất về trải nghiệm. Code chốt, không phải prompt.
    intent = routed["intent"]

    # (3a) Tin nhắn KHÔNG PHẢI yêu cầu tìm gia sư: user dán 1 đoạn văn/mô tả dài (bio gia sư,
    # bài viết...). LLM dễ "nhập vai" theo đoạn text đó hoặc vờ đã hiểu rồi bắn card. Chặn
    # bằng code: text dài mà router KHÔNG trích được tiêu chí mới nào → hỏi lại cho rõ.
    # Guard phải ĐỘC LẬP với router: đo trên chính tin nhắn, không tin intent/filter của LLM.
    #  - filter: dán bio "gia sư Tiếng Anh lớp 7-8" → router trích subject_id=2 từ đoạn text
    #    đó (môn của người trong bài viết, KHÔNG phải nhu cầu người hỏi).
    #  - intent: có history "đang tìm Tiếng Anh" thì router luôn cho "tutor" (đo 5/5 lần),
    #    vì nó tưởng user đang tiếp tục mạch cũ.
    # Dấu hiệu tin cậy nhất là hình dạng tin nhắn: dài + văn phong người viết TỰ GIỚI THIỆU
    # (ngôi thứ nhất, lời mời liên hệ) → chắc chắn không phải câu yêu cầu tìm gia sư.
    _msg = (body.message or "").strip()
    _selfintro_hits = sum(
        1 for kw in ("mình là", "tôi là", "mình sẽ giúp", "phương pháp của tôi",
                     "liên hệ với mình", "hãy liên hệ", "học thử", "anh/chị")
        if kw in _msg.lower()
    )
    if len(_msg) > _LONG_MESSAGE_CHARS and _selfintro_hits >= 2:
        return WebChatResponse(
            reply=(
                "Mình chưa rõ ý bạn ở đoạn này ạ. Bạn muốn tìm gia sư có đặc điểm giống mô tả "
                "trên phải không? Cho mình biết cần học môn gì, lớp mấy để mình tìm giúp nhé."
            ),
            intent="consult",
            filters=prev,
            suggestions=[],
        )

    if intent == "consult":
        has_subject = bool(filters.subject_id or body.context.subject_id)
        has_grade = bool(filters.grade_level_id or body.context.grade_level_id)
        # CHỈ ép khi router trích được tiêu chí MỚI từ chính tin nhắn này. Nếu không, đây là
        # câu router KHÔNG hiểu (user dán 1 đoạn text lạ, hỏi lan man...) mà filter cũ vẫn
        # còn môn+lớp từ lượt trước → ép sang tutor sẽ thành "không hiểu gì vẫn bắn card".
        # Để nguyên consult cho router hỏi lại — đó mới là hành vi đúng.
        said_something = any(v is not None for v in (routed["filters"] or {}).values())
        if has_subject and has_grade and said_something:
            intent = "tutor"
            # Câu router sinh là câu HỎI (nó tưởng còn consult) — giữ lại sẽ thành
            # "hỏi thêm nhưng vẫn bắn card". Bỏ đi để TutorHandler tự sinh câu dẫn kết quả.
            router_reply = ""
        else:
            router_reply = routed["reply"]
    else:
        router_reply = routed["reply"]

    # (4) Dispatch handler theo intent.
    ctx = HandlerContext(
        message=body.message,
        history=history,
        context=body.context,
        filters=filters,
        router_reply=router_reply,
        suggestions=routed["suggestions"],
    )
    handler = _HANDLERS[intent]
    return await handler.handle(ctx)
