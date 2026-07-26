"""
Base handler (pattern Kodee: base handler định nghĩa cấu trúc + chức năng chung; mỗi
specialized handler kế thừa để trở thành 1 agent hoàn chỉnh cho 1 loại nhu cầu).

Mỗi handler nhận cùng 1 HandlerContext (đã qua router) và trả WebChatResponse. Router
điều phối chọn handler theo intent; handler tự lo function/RAG riêng của domain nó.
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
    filters: TutorChatFilters          # đã merge (cũ + mới router trích)
    router_reply: str                  # câu router đã sinh (handler được ghi đè)
    suggestions: list[str] = field(default_factory=list)


class BaseHandler(ABC):
    """Hợp đồng chung. label chỉ để log/nhận diện (giống chatbot_label của Kodee)."""
    label: str = "base"

    @abstractmethod
    async def handle(self, ctx: HandlerContext) -> WebChatResponse:
        ...
