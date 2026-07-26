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


def _merge_filters(prev: TutorChatFilters, new: dict) -> TutorChatFilters:
    """Tích luỹ state: giữ giá trị cũ, chỉ override field router vừa trích (non-null)."""
    merged = prev.model_dump()
    for k, v in new.items():
        if v is not None and k in merged:
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

    # (4) Dispatch handler theo intent.
    ctx = HandlerContext(
        message=body.message,
        history=history,
        context=body.context,
        filters=filters,
        router_reply=routed["reply"],
        suggestions=routed["suggestions"],
    )
    handler = _HANDLERS[routed["intent"]]
    return await handler.handle(ctx)
