"""
Guardrail (pattern Kodee: handoff_classifier chạy ĐẦU luồng để chặn sớm).

Kodee dùng is_seeking_human_assistance() để tách câu cần người thật. Web Tutora chưa có
người thật, nên guardrail ở đây = chặn OFF-TOPIC: câu không liên quan gia sư/Tutora bị từ
chối lịch sự NGAY, không cho chạy pipeline tìm kiếm/RAG (tránh LLM lan man trả lời linh tinh
— đúng mục đích (3) 'từ chối trả lời không liên quan').

Quyết định off-topic do router.route() phân loại (scope='off_topic'); file này chỉ dựng
câu từ chối. Tách riêng để sau dễ thêm luật guardrail khác (chặn nội dung nhạy cảm, spam...)
mà không đụng router.
"""
from __future__ import annotations

# Câu từ chối MẶC ĐỊNH khi router không kèm reply (fallback tất định, không gọi LLM lần 2).
_REFUSAL_FALLBACK = (
    "Dạ mình là trợ lý của Tutora, chỉ hỗ trợ về việc tìm gia sư và các câu hỏi liên quan "
    "đến Tutora thôi ạ. Anh/chị cần tìm gia sư môn gì, cho bé lớp mấy để mình hỗ trợ nhé?"
)


def is_off_topic(scope: str) -> bool:
    return scope == "off_topic"


def refusal_reply(router_reply: str) -> str:
    """Ưu tiên câu từ chối router đã sinh (bám ngữ cảnh); rỗng → fallback tất định."""
    return router_reply or _REFUSAL_FALLBACK
