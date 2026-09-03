"""
Base handler (pattern Kodee: base handler định nghĩa cấu trúc + chức năng chung; mỗi
specialized handler kế thừa để trở thành 1 agent hoàn chỉnh cho 1 loại nhu cầu).
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field

from ...web_agent.schemas import WebChatResponse
from ....models.schemas import TutorChatContext, TutorChatFilters


@dataclass
class HandlerContext:
    """Mọi dữ liệu handler cần — dựng sẵn bởi agent.py sau bước router."""
    message: str
    history: list[dict]
    context: TutorChatContext
    filters: TutorChatFilters
    router_reply: str
    suggestions: list[str] = field(default_factory=list)
    # Entity memory: gia sư đã hiển thị các lượt trước (FE giữ & gửi lại). Dùng để hiểu
    # "thầy A"/"người đầu tiên" trỏ về ai, và làm tập trắng chống bịa gia sư không tồn tại.
    shown_tutors: list = field(default_factory=list)
    # Mong muốn mềm tích luỹ từ các lượt TRƯỚC (chưa gộp phần router vừa trích ở lượt này).
    # Xếp hạng phải dùng NGUYÊN VĂN tin nhắn hiện tại + cái này, KHÔNG dùng bản LLM diễn
    # giải lại lời user — xem _rank_query trong handlers/tutor.py.
    prior_preferences: str | None = None


class BaseHandler(ABC):
    """Hợp đồng chung. label chỉ để log/nhận diện (giống chatbot_label của Kodee)."""
    label: str = "base"

    @abstractmethod
    async def handle(self, ctx: HandlerContext) -> WebChatResponse:
        ...
