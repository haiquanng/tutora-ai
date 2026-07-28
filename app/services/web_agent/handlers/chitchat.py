"""
Chitchat handler — chào hỏi / mở đầu / câu CHƯA nêu nhu cầu tìm gia sư cụ thể.
"""
from __future__ import annotations

from ..schemas import WebChatResponse
from ..handlers.base import BaseHandler, HandlerContext

_DEFAULT_REPLY = (
    "Chào bạn! Mình là trợ lý của Tutora, giúp bạn tìm gia sư phù hợp. "
    "Bạn đang cần tìm gia sư môn gì, cho lớp mấy ạ?"
)

_DEFAULT_SUGGESTIONS = ["Tìm gia sư Toán", "Tìm gia sư Tiếng Anh", "Gia sư học sinh lớp 9?"]


class ChitchatHandler(BaseHandler):
    label = "chitchat"

    async def handle(self, ctx: HandlerContext) -> WebChatResponse:
        return WebChatResponse(
            reply=ctx.router_reply or _DEFAULT_REPLY,
            intent="chitchat",
            filters=ctx.filters,
            suggestions=ctx.suggestions or _DEFAULT_SUGGESTIONS,
        )
