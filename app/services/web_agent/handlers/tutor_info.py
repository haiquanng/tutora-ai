"""
Tutor-info handler — user hỏi VỀ một gia sư đã được gợi ý ("thầy A dạy sao?", "cô kia có
kinh nghiệm không?", "người đầu tiên học phí bao nhiêu?").

Bài toán chuẩn: coreference resolution + grounding.
  - Coreference: "thầy A"/"cô kia"/"người đầu tiên" đều trỏ về 1 tutor_id. LLM không có
    state giữa các lượt, và history chỉ mang text reply (không có tutor_id của card) →
    phải có ENTITY MEMORY: ctx.shown_tutors do FE giữ và gửi lại mỗi lượt.
  - Grounding: dữ liệu lấy THẲNG từ tutor_profiles rồi mới đưa LLM diễn đạt. LLM không
    được tự nhớ/tự suy ra thông tin gia sư — đó là đường dẫn tới bịa.
"""
from __future__ import annotations

import re
import unicodedata

from google.genai import types

from ..schemas import WebChatResponse
from ..handlers.base import BaseHandler, HandlerContext
from ....core.dependencies import get_supabase, get_gemini_client

_MODEL = "gemini-2.5-flash-lite"

# "người đầu tiên", "bạn thứ 2"... → index trong danh sách card đã hiện.
_ORDINALS = [
    (("dau tien", "thu nhat", "so 1", "tren cung", "ban dau"), 0),
    (("thu hai", "thu 2", "so 2", "ke tiep", "con lai"), 1),
    (("thu ba", "thu 3", "so 3"), 2),
]


def _strip_accents(s: str) -> str:
    """Bỏ dấu tiếng Việt. Phải xử lý đ/Đ RIÊNG: nó là ký tự độc lập (U+0111), không phải
    'd' + dấu tổ hợp, nên NFD không tách được → "đầu tiên" vẫn ra "đau tien" nếu bỏ sót."""
    out = "".join(c for c in unicodedata.normalize("NFD", s or "")
                  if unicodedata.category(c) != "Mn").lower()
    return out.replace("đ", "d")


def _focus_from_history(history: list[dict], shown: list) -> object | None:
    """Gia sư đang được NÓI TỚI trong mạch hội thoại (discourse focus).

    "thầy có bằng cấp gì" sau khi vừa hỏi về thầy Nam → chủ ngữ VẪN là thầy Nam. Nếu chỉ
    nhìn tin nhắn hiện tại thì lượt nào cũng phải hỏi lại "bạn muốn hỏi ai?" — đúng lỗi
    user phản ánh. Quét history từ MỚI→CŨ, ai được nhắc gần nhất thì đó là focus.
    """
    for m in reversed(history or []):
        text = _strip_accents(m.get("content") or "")
        if not text:
            continue
        for t in shown:
            name = _strip_accents(getattr(t, "name", "") or "")
            if not name:
                continue
            parts = name.split()
            for n in (len(parts), 2, 1):
                if n < 1 or len(parts) < n:
                    continue
                tail = " ".join(parts[-n:])
                if len(tail) >= 2 and re.search(rf"\b{re.escape(tail)}\b", text):
                    return t
    return None


def _resolve(message: str, shown: list, history: list[dict] | None = None) -> object | None:
    """Tìm gia sư user đang nhắc. None = không chắc → phải hỏi lại, KHÔNG đoán."""
    if not shown:
        return None
    msg = _strip_accents(message)

    # 1) Khớp TÊN — ưu tiên cao nhất, và khớp cả tên rút gọn ("thầy Công" ↔ "LÊ THÀNH CÔNG").
    hits = []
    for t in shown:
        name = _strip_accents(getattr(t, "name", "") or "")
        if not name:
            continue
        if name in msg:
            hits.append((t, len(name)))
            continue
        # Tên riêng thường là 1-2 từ cuối ("Lê Thành Công" → "thanh cong", "cong");
        # người Việt hay gọi tắt bằng ĐÚNG 1 âm tiết cuối ("thầy Tú", "cô Hương").
        parts = name.split()
        for n in (2, 1):
            if len(parts) >= n:
                tail = " ".join(parts[-n:])
                # \b để "cong" không khớp nhầm trong "khong", "cong viec"...
                if len(tail) >= 2 and re.search(rf"\b{re.escape(tail)}\b", msg):
                    hits.append((t, len(tail)))
                    break
    if hits:
        # Nhiều người khớp (2 gia sư cùng tên) → mơ hồ thật sự, để hỏi lại.
        best = max(h[1] for h in hits)
        top = [h[0] for h in hits if h[1] == best]
        return top[0] if len(top) == 1 else None

    # 2) Khớp THỨ TỰ ("người đầu tiên") — chỉ khi user không nêu tên.
    for words, idx in _ORDINALS:
        if any(w in msg for w in words) and idx < len(shown):
            return shown[idx]

    # 3) Chỉ có ĐÚNG 1 người từng hiện → "thầy đó" chắc chắn là người này.
    if len(shown) == 1:
        return shown[0]

    # 4) DISCOURSE FOCUS: câu không nêu tên ("thầy có bằng cấp gì", "đúng rồi") nhưng đang
    # tiếp mạch về một người → lấy người được nhắc gần nhất trong hội thoại.
    return _focus_from_history(history or [], shown)


_DAY_NAMES = {1: "Thứ Hai", 2: "Thứ Ba", 3: "Thứ Tư", 4: "Thứ Năm",
              5: "Thứ Sáu", 6: "Thứ Bảy", 7: "Chủ Nhật"}

# tutor_availability lưu giờ theo UTC
_VN_OFFSET_HOURS = 7


def _to_vn(hhmm: str | None) -> tuple[str, int]:
    """'HH:MM[:SS]' UTC → ('HH:MM' giờ VN, số ngày bị đẩy sang). Cộng 7h có thể vượt 24h."""
    if not hhmm:
        return "", 0
    try:
        h, m = int(hhmm[:2]), int(hhmm[3:5])
    except (ValueError, IndexError):
        return "", 0
    total = h + _VN_OFFSET_HOURS
    return f"{total % 24:02d}:{m:02d}", total // 24


def _slot_label(a: dict) -> str:
    """1 khoảng rảnh → nhãn giờ VN. Nếu +7 làm sang ngày hôm sau thì đổi luôn tên thứ."""
    dow = a.get("day_of_week_id")
    if not dow:
        return ""
    start, carry = _to_vn(a.get("start_time"))
    end, _ = _to_vn(a.get("end_time"))
    if not start or not end:
        return ""
    # vd 18:00 UTC Thứ Bảy → 01:00 Chủ Nhật (giờ VN).
    day = _DAY_NAMES.get((dow + carry - 1) % 7 + 1, "")
    return f"{day} {start}-{end}"


def _load_profile(tutor_id: str) -> dict | None:
    """Hồ sơ THẬT từ DB — nguồn duy nhất cho câu trả lời (grounding)."""
    sb = get_supabase()
    rows = (sb.table("tutor_profiles")
            .select("tutor_id, headline, bio, education, degree, gpa, gpa_scale, experience, "
                    "teaching_mode, teaching_area_city, average_rating, total_reviews, "
                    "completed_hours, profile_status, is_public")
            .eq("tutor_id", tutor_id).limit(1).execute().data or [])
    if not rows:
        return None
    p = rows[0]
    if p.get("profile_status") != "active" or not p.get("is_public"):
        return None

    name = ""
    u = (sb.table("users").select("full_name").eq("user_id", tutor_id).limit(1).execute().data or [])
    if u:
        name = u[0].get("full_name") or ""

    prices = (sb.table("tutor_subject_grade_prices")
              .select("subject_id, grade_level_id, price_per_hour, is_active")
              .eq("tutor_id", tutor_id).eq("is_active", True).execute().data or [])
    subj_ids = {r["subject_id"] for r in prices if r.get("subject_id")}
    grade_ids = {r["grade_level_id"] for r in prices if r.get("grade_level_id")}
    subjects = grades = []
    if subj_ids:
        subjects = [r["subject_name"] for r in (sb.table("subjects")
                    .select("subject_id, subject_name").in_("subject_id", list(subj_ids))
                    .execute().data or [])]
    if grade_ids:
        grades = [r["grade_name"] for r in (sb.table("grade_levels")
                  .select("grade_level_id, grade_name").in_("grade_level_id", list(grade_ids))
                  .execute().data or [])]
    amounts = [float(r["price_per_hour"]) for r in prices if r.get("price_per_hour") is not None]

    avail = (sb.table("tutor_availability")
             .select("day_of_week_id, start_time, end_time")
             .eq("tutor_id", tutor_id).execute().data or [])
    slots = sorted({s for s in (_slot_label(a) for a in avail) if s})

    p.update({"full_name": name, "subjects": subjects, "grades": grades,
              "price_min": min(amounts) if amounts else None,
              "price_max": max(amounts) if amounts else None,
              "slots": slots})
    return p


def _facts_text(p: dict) -> str:
    """Chỉ liệt kê field CÓ dữ liệu — thiếu thì bỏ hẳn, để LLM không có cớ suy diễn."""
    L = []
    add = lambda k, v: L.append(f"- {k}: {v}") if v else None
    add("Tên", p.get("full_name"))
    add("Giới thiệu ngắn", p.get("headline"))
    add("Học vấn", p.get("education"))
    add("Bằng cấp", p.get("degree"))
    if p.get("gpa"):
        L.append(f"- GPA: {p['gpa']}/{p.get('gpa_scale') or 4}")
    add("Môn dạy", ", ".join(p.get("subjects") or []))
    add("Khối lớp", ", ".join(p.get("grades") or []))
    mode = {"online": "Dạy online", "offline": "Dạy tại nhà",
            "both": "Online và tại nhà"}.get(str(p.get("teaching_mode") or "").lower())
    add("Hình thức", mode)
    add("Khu vực", p.get("teaching_area_city"))
    if p.get("price_min"):
        rng = (f'{int(p["price_min"]):,}đ/giờ' if p["price_min"] == p.get("price_max")
               else f'{int(p["price_min"]):,}–{int(p["price_max"]):,}đ/giờ')
        L.append(f"- Học phí: {rng}".replace(",", "."))
    if p.get("total_reviews"):
        L.append(f'- Đánh giá: {p.get("average_rating")}/5 từ {p["total_reviews"]} lượt')
    else:
        L.append("- Đánh giá: chưa có lượt đánh giá nào")
    L.append(f'- Số giờ đã dạy: {p.get("completed_hours") or 0}')
    add("Lịch rảnh", "; ".join(p.get("slots") or []))
    add("Kinh nghiệm (gia sư tự mô tả)", (p.get("experience") or "").strip()[:600])
    add("Mô tả bản thân (gia sư tự viết)", (p.get("bio") or "").strip()[:600])
    return "\n".join(L)


_ASK_WHICH = (
    "Bạn muốn hỏi về gia sư nào ạ? Bạn cho mình biết tên gia sư để mình xem giúp nhé."
)
_NOT_FOUND = (
    "Hồ sơ gia sư này hiện chưa xem được, bạn thử chọn gia sư khác giúp mình nhé."
)


class TutorInfoHandler(BaseHandler):
    label = "tutor_info"

    async def handle(self, ctx: HandlerContext) -> WebChatResponse:
        shown = getattr(ctx, "shown_tutors", None) or []
        target = _resolve(ctx.message, shown, ctx.history)

        # REPAIR TURN: không chắc là ai → HỎI LẠI. Không đoán, không trả lời chung chung.
        if target is None:
            names = [getattr(t, "name", "") for t in shown if getattr(t, "name", "")]
            reply = _ASK_WHICH
            if names:
                reply = (f"Bạn muốn hỏi về {' hay '.join(names[:3])} ạ? "
                         "Cho mình biết tên để mình xem giúp nhé.")
            return WebChatResponse(reply=reply, intent="tutor_info", filters=ctx.filters)

        profile = _load_profile(getattr(target, "tutor_id", ""))
        if not profile:
            return WebChatResponse(reply=_NOT_FOUND, intent="tutor_info", filters=ctx.filters)

        reply = _answer(ctx.message, _facts_text(profile), ctx.history)
        # KHÔNG trả cards: user đang hỏi, không xin danh sách mới. Bắn lại card ở đây là
        # spam (bug thật đã gặp ở luồng Zalo 2026-07-11).
        return WebChatResponse(
            reply=reply or _NOT_FOUND, intent="tutor_info",
            filters=ctx.filters, suggestions=ctx.suggestions,
        )


def _answer(question: str, facts: str, history: list[dict]) -> str:
    """LLM chỉ DIỄN ĐẠT lại facts — không được thêm thông tin ngoài danh sách."""
    convo = "\n".join(f'{m["role"]}: {m["content"]}' for m in history[-6:])
    prompt = (
        "Bạn là trợ lý Tutora. Trả lời câu hỏi của phụ huynh/học sinh về MỘT gia sư, DỰA "
        "HOÀN TOÀN vào thông tin hồ sơ bên dưới. TUYỆT ĐỐI không thêm chi tiết nào ngoài đó "
        "(không suy đoán về tính cách, phương pháp dạy, chất lượng nếu hồ sơ không nói).\n"
        "Nếu hồ sơ KHÔNG có thông tin user hỏi, nói thẳng là hồ sơ chưa có mục đó và gợi ý "
        "họ nhắn trực tiếp cho gia sư qua nền tảng.\n"
        "Giọng thân thiện, ngắn gọn (2-4 câu), tiếng Việt, xưng 'mình'. Không bịa.\n"
        "TUYỆT ĐỐI KHÔNG chào hỏi/giới thiệu lại bản thân ('Chào bạn', 'mình là trợ lý "
        "Tutora'...) — đang giữa cuộc trò chuyện, chào lại mỗi lượt rất máy móc. "
        "Vào thẳng nội dung trả lời.\n\n"
        f"HỒ SƠ GIA SƯ:\n{facts}\n\n"
        f"{('Hội thoại gần đây:' + chr(10) + convo + chr(10) + chr(10)) if convo else ''}"
        f"Câu hỏi: {question}"
    )
    try:
        resp = get_gemini_client().models.generate_content(
            model=_MODEL, contents=prompt,
            config=types.GenerateContentConfig(
                temperature=0.2,
                thinking_config=types.ThinkingConfig(thinking_budget=0),
            ),
        )
        return (resp.text or "").strip()
    except Exception as e:
        print(f"web tutor_info answer error: {e}")
        return ""
