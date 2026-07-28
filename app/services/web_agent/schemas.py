"""
Web là chatbot tự do 3 mục đích:
  1. Gợi ý gia sư  → trả card (TutorCard) kèm link tới trang gia sư.
  2. Hỏi hệ thống  → RAG trên KB Tutora (chính sách, cách hoạt động).
  3. Lạc đề        → từ chối lịch sự (guardrail chặn trước).

Stateless như mọi service AI: FE giữ history + filters tích luỹ, gửi kèm mỗi lượt.
"""
from __future__ import annotations

from typing import Optional, List, Literal
from pydantic import BaseModel, field_validator

from ...models.schemas import HistoryMessage, TutorChatContext, TutorChatFilters


class WebChatRequest(BaseModel):
    history: List[HistoryMessage] = []
    message: str = ""
    context: TutorChatContext = TutorChatContext()
    # Filter tích luỹ qua các lượt (FE giữ & gửi lại) — merge, không reset.
    current_filters: Optional[TutorChatFilters] = None

    # FE/.NET gửi `context: null` tường minh khi chưa có ngữ cảnh — default chỉ áp dụng
    # khi field VẮNG, không phải khi = null → Pydantic reject (422). Coerce null → default.
    @field_validator("context", mode="before")
    @classmethod
    def _default_context(cls, v):
        return TutorChatContext() if v is None else v


class TutorCard(BaseModel):
    """1 card gia sư hiển thị trong bong bóng chat (mẫu tham khảo card của Kodee:
    badge 'phù hợp nhất', giá nổi bật, list gạch đầu dòng có ✓, nút CTA).
    profile_url để FE điều hướng tới trang chi tiết gia sư (khác Zalo mở Mini App)."""
    tutor_id: str
    name: str
    avatar_url: Optional[str] = None
    # Badge "Phù hợp nhất" — chỉ gắn cho gia sư đầu danh sách (top ranking).
    is_best_match: bool = False
    price_per_hour: Optional[float] = None
    rating: Optional[float] = None
    total_reviews: Optional[int] = None
    # Điểm mạnh dạng gạch đầu dòng (kinh nghiệm, môn dạy, hình thức...) — mỗi dòng 1 ✓.
    highlights: List[str] = []
    profile_url: str
    cta_label: str = "Xem chi tiết"


class WebChatResponse(BaseModel):
    reply: str
    # scope='off_topic' → reply là câu từ chối lịch sự, cards rỗng.
    # intent='tutor' → cards có dữ liệu; intent='faq' → reply là câu trả lời RAG;
    # intent='chitchat' → chào hỏi; intent='consult' → đang tư vấn hỏi thêm nhu cầu (chưa card).
    intent: Literal["tutor", "consult", "faq", "off_topic", "chitchat"] = "tutor"
    cards: List[TutorCard] = []
    filters: TutorChatFilters = TutorChatFilters()
    ai_ranked: bool = False
    # Gợi ý câu hỏi tiếp theo cho user bấm (đổi môn, hỏi thêm...) — giống Zalo suggestions.
    suggestions: List[str] = []
