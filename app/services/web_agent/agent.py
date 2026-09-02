"""
Web agent — điều phối chatbot tự do 3 mục đích cho Tutora-FE

Kiến trúc theo pattern Kodee (guardrail → router → specialized handler), 

Luồng 1 lượt:
  1. Lấy subjects (.NET) + merge filter tích luỹ (FE gửi) với filter mới router trích.
  2. router.route() → {scope, intent, reply, suggestions, filters}.
  3. scope='off_topic' → guardrail từ chối lịch sự, DỪNG (không search/RAG).
  4. intent='tutor' → TutorHandler (function .NET recommend → card).
     intent='faq'   → FaqHandler (RAG KB Tutora, chống bịa).
"""
from __future__ import annotations

import unicodedata

from .schemas import WebChatRequest, WebChatResponse
from . import router as router_mod
from . import guardrail
from .handlers.base import HandlerContext
from .handlers.tutor import TutorHandler
from .handlers.faq import FaqHandler
from .handlers.chitchat import ChitchatHandler
from .handlers.consult import ConsultHandler, ASKED_MARK as _ASKED_MARK
from .handlers.tutor_info import TutorInfoHandler
from ..tutoring_shared.candidates import _get_subjects, _get_grades
from ...models.schemas import TutorChatFilters, ShownTutor

# Đăng ký handler theo intent (Kodee: agent_router map label → handler).
_HANDLERS = {
    "tutor": TutorHandler(),
    "consult": ConsultHandler(),
    "tutor_info": TutorInfoHandler(),
    "faq": FaqHandler(),
    "chitchat": ChitchatHandler(),
}


# Sentinel router dùng để XOÁ 1 filter (khác null = "không nhắc, giữ nguyên").
_CLEAR = "__clear__"

# Ngưỡng coi tin nhắn là "đoạn văn dán vào" chứ không phải câu yêu cầu.
_LONG_MESSAGE_CHARS = 220


# Trần độ dài preferences: đây là text nhồi vào query embedding, để trôi vô hạn thì mong
# muốn cũ sẽ pha loãng mong muốn mới và làm nhiễu xếp hạng.
_MAX_PREFERENCES_CHARS = 300


def _append_preference(prev: str | None, new: str) -> str:
    new = (new or "").strip()
    if not prev:
        return new[:_MAX_PREFERENCES_CHARS]
    if not new or new.lower() in prev.lower():
        return prev
    return f"{prev}, {new}"[:_MAX_PREFERENCES_CHARS]


# Dấu hiệu user muốn NGƯỜI KHÁC / hỏi chung cả nhóm, chứ không hỏi về 1 gia sư đã hiện.
# "co ai" tách biệt với "co ay" (cô ấy) sau khi bỏ dấu nên không đụng nhau.
_SEEK_OTHER_PATTERNS = (
    "nguoi khac", "gia su khac", "ai khac", "thay khac", "co khac",
    "tim them", "xem them", "tim nguoi", "doi gia su", "doi nguoi",
    "co ai", "ai co", "gia su nao", "nguoi nao", "ai la nguoi",
)


def _no_accent(text: str) -> str:
    out = "".join(c for c in unicodedata.normalize("NFD", text or "")
                  if unicodedata.category(c) != "Mn").lower()
    return out.replace("đ", "d")


def _seeks_other_tutor(message: str) -> bool:
    return any(p in _no_accent(message) for p in _SEEK_OTHER_PATTERNS)


def _merge_filters(prev: TutorChatFilters, new: dict) -> TutorChatFilters:
    """Tích luỹ state: giữ giá trị cũ, chỉ override field router vừa trích (non-null).
    """
    merged = prev.model_dump()
    for k, v in new.items():
        if k not in merged:
            continue
        if v == _CLEAR:
            merged[k] = None
        elif v is not None:
            # preferences là tiêu chí MỀM tích luỹ: user nêu thêm mong muốn ở lượt sau thì
            # cộng vào, không đè mất cái cũ ("con mất gốc" lượt 1 + "cần cô kiên nhẫn"
            # lượt 3 = cả hai đều phải vào query xếp hạng). Các trục khác thì ghi đè, vì
            # chúng là giá trị đơn (giá mới thay giá cũ).
            merged[k] = _append_preference(merged.get(k), v) if k == "preferences" else v
    return TutorChatFilters(**merged)


async def web_chat(body: WebChatRequest) -> WebChatResponse:
    history = [m.model_dump() for m in body.history]
    subjects = await _get_subjects()
    grades = await _get_grades()
    prev = body.current_filters or TutorChatFilters()

    # (2) Router: 1 call phân scope + intent + trích filter (gồm lớp) + reply.
    routed = await router_mod.route(history, body.message, prev, subjects, grades,
                                    shown_tutors=body.shown_tutors)

    # (3) Guardrail: off-topic → từ chối, dừng ngay.
    if guardrail.is_off_topic(routed["scope"]):
        return WebChatResponse(
            reply=guardrail.refusal_reply(routed["reply"]),
            intent="off_topic",
            suggestions=routed["suggestions"],
        )

    filters = _merge_filters(prev, routed["filters"])

    intent = routed["intent"]

    # Router hay nhầm "xin gia sư KHÁC" thành tutor_info, vì câu vẫn nhắc tới mấy người
    # vừa hiện ("2 người đó không có chứng chỉ, tôi muốn tìm người khác có chứng chỉ").
    # Hậu quả: handler tutor_info đi kể hồ sơ của đúng người user vừa nói là KHÔNG muốn.
    # Chốt bằng code — câu xin người khác có dấu hiệu rất rõ, không cần LLM đoán.
    if intent == "tutor_info" and _seeks_other_tutor(body.message):
        intent = "tutor"
        routed["reply"] = ""   # câu router sinh là câu hỏi lại, giữ sẽ lệch hẳn ngữ cảnh

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

    # Điều kiện ĐỦ để giới thiệu gia sư: có MÔN + LỚP (bắt buộc) VÀ ít nhất 1 tiêu chí nữa
    # (giá / giới tính / lịch rảnh / số lượng).
    has_subject = bool(filters.subject_id or body.context.subject_id)
    has_grade = bool(filters.grade_level_id or body.context.grade_level_id)
    extra = sum(1 for v in (
        filters.min_rate, filters.max_rate, filters.tutor_gender,
        getattr(filters, "available_days", None), filters.desired_count,
    ) if v)
    enough = has_subject and has_grade and extra >= 1

    # Gate "hỏi đúng 1 lần"
    asked_before = any(
        m.get("role") == "assistant" and _ASKED_MARK in (m.get("content") or "")
        for m in history
    )

    if intent == "consult":
        said_something = any(v is not None for v in (routed["filters"] or {}).values())
        if has_subject and has_grade and said_something and (enough or asked_before):
            intent = "tutor"
            # Câu router sinh là câu HỎI (nó tưởng còn consult) — giữ lại sẽ thành
            # "hỏi thêm nhưng vẫn bắn card".
            router_reply = ""
        else:
            router_reply = routed["reply"]
    elif intent == "tutor" and not enough and not asked_before:
        # Router muốn bắn card khi mới có môn+lớp → chặn lại, hỏi thêm 1 câu trước.
        intent = "consult"
        router_reply = ""
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
        shown_tutors=body.shown_tutors,
        prior_preferences=prev.preferences,
    )
    handler = _HANDLERS[intent]
    resp = await handler.handle(ctx)

    if resp.cards:
        resp.shown_tutors = [ShownTutor(tutor_id=c.tutor_id, name=c.name) for c in resp.cards]
    else:
        resp.shown_tutors = body.shown_tutors
    return resp
