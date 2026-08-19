"""
Tutor handler (specialized handler — Kodee: 1 handler = 1 agent hoàn chỉnh cho 1 domain).

Function của handler này: search_tutors → gọi Ranking Core (.NET /recommend) qua
_fetch_candidates (tutoring_shared). LLM KHÔNG bịa gia sư — chỉ code gọi function lấy data
thật rồi map sang card (đúng nguyên tắc Kodee: function lấy data thật chống hallucination).
"""
from __future__ import annotations

from ..schemas import WebChatResponse, TutorCard
from ..handlers.base import BaseHandler, HandlerContext
from ...tutoring_shared.candidates import _fetch_candidates

_PROFILE_PATH = "/tutor-detail/{tutor_id}"

_MAX_CARDS = 2   # số card hiển thị trong 1 lượt (gọn cho bong bóng chat)


def _to_card(t: dict, is_best: bool) -> TutorCard:
    """Map 1 item .NET TutorRecommendItem → TutorCard. Highlights: chọn tín hiệu MẠNH
    nhất, ngắn gọn (kinh nghiệm giờ dạy, số review) — mỗi dòng 1 gạch đầu dòng có ✓ ở FE."""
    highlights: list[str] = []
    hours = t.get("completedHours") or t.get("completed_hours")
    if hours:
        highlights.append(f"{int(hours)}+ giờ đã dạy")
    reviews = t.get("totalReviews") or t.get("total_reviews")
    if reviews:
        highlights.append(f"{int(reviews)} lượt đánh giá")
    mode = t.get("teachingMode") or t.get("teaching_mode")
    if mode:
        label = {"online": "Dạy online", "offline": "Dạy tại nhà", "both": "Online & tại nhà"}.get(
            str(mode).lower(), str(mode))
        highlights.append(label)

    tid = str(t.get("tutorId") or t.get("tutor_id") or "")
    return TutorCard(
        tutor_id=tid,
        name=t.get("fullName") or t.get("name") or "Gia sư",
        avatar_url=t.get("avatarUrl") or t.get("avatar_url"),
        is_best_match=is_best,
        price_per_hour=t.get("pricePerHour") or t.get("hourlyRate") or t.get("priceMin"),
        rating=t.get("averageRating") or t.get("average_rating"),
        total_reviews=reviews,
        highlights=highlights,
        profile_url=_PROFILE_PATH.format(tutor_id=tid),
    )


class TutorHandler(BaseHandler):
    label = "tutor"

    async def handle(self, ctx: HandlerContext) -> WebChatResponse:
        # Guard: chưa có tiêu chí tối thiểu (không môn từ filter/context, message rỗng) thì
        # tìm gia sư là vô nghĩa → hỏi lại nhu cầu thay vì gọi .NET recommend rỗng.
        has_subject = bool(ctx.filters.subject_id or ctx.context.subject_id)
        if not has_subject and not (ctx.message or "").strip():
            return WebChatResponse(
                reply=ctx.router_reply
                or "Bạn muốn tìm gia sư môn gì, cho lớp mấy để mình gợi ý phù hợp nhé?",
                intent="chitchat", filters=ctx.filters, suggestions=ctx.suggestions,
            )
        try:
            content = await _fetch_candidates(ctx.context, ctx.filters, ctx.message)
            tutors = content.get("tutors", []) or []
            ai_ranked = bool(content.get("aiRanked", False))
            # Thứ tự do Ranking Core quyết (Bayesian + blend). Chỉ khi core fail (SQL order)
            # mới hạ gia sư 0-review xuống cuối — giống luồng Zalo, giữ nhất quán ranking.
            if not ai_ranked:
                tutors.sort(key=lambda t: (t.get("totalReviews") or 0) == 0)
        except Exception as e:
            print(f"web tutor handler error: {e}")
            return WebChatResponse(
                reply="Xin lỗi, mình chưa tải được danh sách gia sư. Bạn thử lại giúp mình nhé.",
                intent="tutor", filters=ctx.filters,
            )

        shown = tutors[:_MAX_CARDS]
        cards = [_to_card(t, is_best=(i == 0 and ai_ranked)) for i, t in enumerate(shown)]

        # Ranking degrade (.NET không gọi được AI rank) → `query` bị BỎ, kết quả chỉ là
        # SQL lọc cứng. Nếu user có nêu mong muốn ngữ nghĩa (bằng cấp, kinh nghiệm...) thì
        # câu router sinh dễ khẳng định sai → thay bằng câu trung thực, không hứa hão.
        if cards and not ai_ranked:
            reply = (
                f"Mình tìm được {len(cards)} gia sư khớp môn/lớp và mức giá bạn cần. "
                "Hiện mình chưa xếp hạng được theo các mong muốn chi tiết (bằng cấp, "
                "kinh nghiệm…), bạn xem thử rồi cho mình biết để lọc kỹ hơn nhé."
            )
            return WebChatResponse(
                reply=reply, intent="tutor", cards=cards,
                filters=ctx.filters, ai_ranked=False, suggestions=ctx.suggestions,
            )

        # Reply: ưu tiên câu router đã sinh; rỗng/không gia sư → câu mặc định rõ ràng
        # (KHÔNG để user hiểu nhầm danh sách cũ là kết quả — bài học từ luồng Zalo).
        reply = ctx.router_reply
        if not cards:
            reply = (reply + " " if reply else "") + (
                "Tiếc là chưa có gia sư nào khớp tiêu chí này. Bạn thử nới bớt yêu cầu "
                "(giá, môn, khu vực…) nhé?"
            )
        elif not reply:
            # Cho xem kết quả TRƯỚC rồi mời tinh chỉnh — thay vì chặn lại hỏi ngân sách/mục
            # tiêu trước khi cho xem gì (hành vi "phỏng vấn thủ tục" user đã phản ánh).
            # Chỉ mời bổ sung tiêu chí user CHƯA nêu, không hỏi lại cái đã biết.
            reply = f"Mình tìm được {len(cards)} gia sư phù hợp:"
            missing = []
            if not (ctx.filters.min_rate or ctx.filters.max_rate):
                missing.append("ngân sách")
            if not ctx.filters.tutor_gender:
                missing.append("giới tính gia sư")
            if missing:
                reply += (
                    f" Bạn muốn lọc sát hơn theo {' hoặc '.join(missing)} thì cho mình biết nhé."
                )

        return WebChatResponse(
            reply=reply, intent="tutor", cards=cards,
            filters=ctx.filters, ai_ranked=ai_ranked, suggestions=ctx.suggestions,
        )
