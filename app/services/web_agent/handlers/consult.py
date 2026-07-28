"""
Consult handler — user muốn tìm gia sư nhưng CHƯA đủ thông tin để tư vấn đúng.

Đây là tầng TƯ VẤN trước khi recommend. KHÔNG gọi Ranking Core:
chỉ trả câu HỎI dẫn dắt (router đã sinh) + gợi ý bấm tiếp. Khi đã đủ lớp + đã hỏi nhu cầu,
router chuyển intent sang "tutor" và TutorHandler mới thật sự giới thiệu gia sư.
"""
from __future__ import annotations

from ..schemas import WebChatResponse
from ..handlers.base import BaseHandler, HandlerContext

_DEFAULT_REPLY = (
    "Để mình gợi ý gia sư phù hợp nhất, bạn cho mình biết bé học lớp mấy, "
    "đang cần củng cố hay nâng cao, và ngân sách mong muốn khoảng bao nhiêu ạ?"
)

class ConsultHandler(BaseHandler):
    label = "consult"

    async def handle(self, ctx: HandlerContext) -> WebChatResponse:
        # KHÔNG recommend — giữ filter đã tích luỹ để lượt sau (khi đủ info) search đúng.
        return WebChatResponse(
            reply=ctx.router_reply or _DEFAULT_REPLY,
            intent="consult",
            filters=ctx.filters,
            suggestions=ctx.suggestions,
        )
