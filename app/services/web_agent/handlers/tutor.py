"""
Tutor handler (specialized handler — Kodee: 1 handler = 1 agent hoàn chỉnh cho 1 domain).

Function của handler này: search_tutors → gọi Ranking Core (.NET /recommend) qua
_fetch_candidates (tutoring_shared). LLM KHÔNG bịa gia sư — chỉ code gọi function lấy data
thật rồi map sang card (đúng nguyên tắc Kodee: function lấy data thật chống hallucination).
"""
from __future__ import annotations

import json
import re
import unicodedata

from google.genai import types

from ..schemas import WebChatResponse, TutorCard
from ..handlers.base import BaseHandler, HandlerContext
from ...tutoring_shared.candidates import _fetch_candidates, _get_subjects, _get_grades
from ....core.dependencies import get_gemini_client
from ...telemetry.usage import track

_MODEL = "gemini-2.5-flash-lite"

_PROFILE_PATH = "/tutor-detail/{tutor_id}"

_MAX_CARDS = 2   # số card hiển thị trong 1 lượt (gọn cho bong bóng chat)


# "còn ai khác không", "gia sư khác đi", "xem thêm người nữa"... — user KHÔNG đổi tiêu chí,
# chỉ muốn thấy NGƯỜI KHÁC. Bắt bằng CODE chứ không hỏi thêm LLM: cách nói rất khuôn mẫu,
# thêm 1 field vào prompt router thì tốn 1 lượt suy luận nữa và có thể lệch ngẫu nhiên.
_MORE_PATTERNS = (
    "ai khac", "nguoi khac", "gia su khac", "co khac", "thay khac",
    "ai nua", "nguoi nua", "gia su nua", "khac khong", "nua khong",
    "xem them", "tim them", "goi y them", "con ai", "con gia su",
    "doi gia su", "doi nguoi", "khong thich", "khong ung",
)


def _strip_accents(text: str) -> str:
    """Bỏ dấu để khớp bất kể user gõ có dấu hay không. đ/Đ là ký tự độc lập (U+0111),
    NFD không tách được nên phải thay tay."""
    out = "".join(c for c in unicodedata.normalize("NFD", text or "")
                  if unicodedata.category(c) != "Mn").lower()
    return out.replace("đ", "d")


def _wants_more(message: str) -> bool:
    msg = _strip_accents(message)
    return any(p in msg for p in _MORE_PATTERNS)


def _exclude_ids(ctx, wants_more: bool) -> list[str]:
    """Tập gia sư cần loại. CHỈ tích luỹ khi user xin người khác; câu tìm kiếm bình thường
    trả [] để pool được làm mới.

    Vì sao phải xoá: exclude tích luỹ mãi sẽ âm thầm loại hết gia sư tốt nhất, đến lúc user
    đổi hẳn tiêu chí (môn khác, lớp khác) vẫn còn dính danh sách cũ → ra ít kết quả hoặc
    rỗng mà không ai hiểu vì sao.
    """
    if not wants_more:
        return []
    prev = list(getattr(ctx.filters, "exclude_tutor_ids", None) or [])
    # shown_tutors = card của ĐÚNG lượt trước (agent.py ghi đè mỗi lần có card), nên phải
    # cộng dồn với prev thì hỏi "còn ai khác" lần 2, lần 3 mới không lặp lại người cũ.
    prev += [tid for t in ctx.shown_tutors
             if (tid := getattr(t, "tutor_id", None)) and tid not in prev]
    return prev


def _follow_up_question(ctx, current_reply: str) -> str:
    """1 câu gợi mở về tiêu chí user CHƯA nêu (giá / giới tính / lịch rảnh).
    """
    missing = []
    if not (ctx.filters.min_rate or ctx.filters.max_rate):
        missing.append("mức học phí mong muốn")
    if not getattr(ctx.filters, "available_days", None):
        missing.append("buổi/khung giờ học được")
    if not ctx.filters.tutor_gender:
        missing.append("muốn thầy hay cô")
    if not missing:
        return ""

    prompt = (
        "Bạn là trợ lý Tutora, vừa gửi danh sách gia sư cho phụ huynh/học sinh.\n"
        f"Câu bạn vừa nói: \"{current_reply}\"\n\n"
        "Viết THÊM ĐÚNG MỘT câu ngắn (tối đa 25 từ), thân thiện, tiếng Việt, xưng 'mình', "
        "gợi mở để họ nói thêm MỘT trong các tiêu chí sau nhằm lọc sát hơn:\n"
        + "\n".join(f"- {m}" for m in missing) +
        "\n\nYÊU CẦU:\n"
        "- Chỉ 1 câu, KHÔNG liệt kê hết mọi tiêu chí, chọn 1-2 cái tự nhiên nhất.\n"
        "- Giọng mời gọi nhẹ nhàng, cho họ thoải mái BỎ QUA nếu không cần.\n"
        "- KHÔNG lặp lại nội dung câu trên, KHÔNG chào lại, KHÔNG nhắc tên gia sư.\n"
        "- Chỉ trả về câu đó, không giải thích."
    )
    try:
        with track("web_agent_tutor", _MODEL) as _t:
            resp = _t.done(get_gemini_client().models.generate_content(
                model=_MODEL, contents=prompt,
                config=types.GenerateContentConfig(
                    temperature=0.7,   # cao hơn các call khác: cần đa dạng, không lặp mỗi lượt
                    thinking_config=types.ThinkingConfig(thinking_budget=0),
                ),
            ))
        return (resp.text or "").strip().strip('"')
    except Exception as e:
        print(f"web tutor follow-up error: {e}")
        return ""


# ─────────────────── MÔ TẢ FILTER ĐÃ THỰC SỰ ÁP DỤNG ───────────────────
# Câu mở đầu PHẢI sinh từ đây, KHÔNG để LLM diễn đạt lại yêu cầu của user.
#
# Bug thật 2026-09-02: user hỏi "gia sư rảnh CẢ T7 và CN", SQL của .NET lọc theo kiểu
# "MỘT TRONG các ngày" (Any + Contains), trả về gia sư chỉ rảnh T7 — nhưng LLM đọc tin nhắn
# user rồi khẳng định "có gia sư rảnh cả Thứ 7 và Chủ Nhật ạ". Lời nói và dữ liệu chạy hai
# đường: không exception, không kết quả rỗng, chỉ có một câu sai kiểm chứng được.
#
# Nguyên tắc (siết thêm từ ghi chú ở router.py):
#   - Lọc CỨNG và đúng ngữ nghĩa  → được khẳng định "đã lọc theo..."
#   - Đẩy vào xếp hạng ngữ nghĩa  → chỉ được nói "ưu tiên..."
#   - Không hỗ trợ                → phải nói thẳng "mình chưa lọc được theo..."
_DAY_LABELS = {1: "Thứ Hai", 2: "Thứ Ba", 3: "Thứ Tư", 4: "Thứ Năm",
               5: "Thứ Sáu", 6: "Thứ Bảy", 7: "Chủ Nhật"}


def _money(v) -> str:
    return f"{int(v):,}".replace(",", ".") + "đ/giờ"


def _join_vi(parts: list[str], last: str = "và") -> str:
    if len(parts) <= 1:
        return "".join(parts)
    return f"{', '.join(parts[:-1])} {last} {parts[-1]}"


async def _applied_criteria(ctx) -> list[str]:
    """Liệt kê ĐÚNG những gì đã gửi xuống SQL — không hơn. Tên môn/lớp tra từ .NET (đã
    cache trong process) vì filter chỉ giữ id, mà user thì nghĩ bằng tên."""
    f = ctx.filters
    out: list[str] = []

    subject_id = f.subject_id or ctx.context.subject_id
    if subject_id:
        name = next((s.get("subjectName") for s in await _get_subjects()
                     if s.get("subjectId") == subject_id), None)
        out.append(f"môn {name}" if name else "đúng môn bạn cần")

    grade_id = getattr(f, "grade_level_id", None) or ctx.context.grade_level_id
    if grade_id:
        name = next((g.get("gradeName") for g in await _get_grades()
                     if g.get("gradeLevelId") == grade_id), None)
        if name:
            out.append(name.lower() if name.lower().startswith("lớp") else f"lớp {name}")

    if f.max_rate and f.min_rate:
        out.append(f"học phí {_money(f.min_rate)}–{_money(f.max_rate)}")
    elif f.max_rate:
        out.append(f"học phí dưới {_money(f.max_rate)}")
    elif f.min_rate:
        out.append(f"học phí từ {_money(f.min_rate)}")

    gender = f.tutor_gender or ctx.context.tutor_gender
    if gender:
        out.append("gia sư nữ" if str(gender).lower() in ("female", "nữ", "nu") else "gia sư nam")

    days = getattr(f, "available_days", None)
    if days:
        labels = _join_vi([_DAY_LABELS[d] for d in sorted(days) if d in _DAY_LABELS])
        # match_all=False nghĩa là SQL chỉ đòi rảnh MỘT TRONG các ngày — phải nói đúng như
        # vậy, đừng để user tưởng đã lọc "cả hai ngày".
        if labels:
            joined = labels if getattr(f, "available_days_match", None) == "all" else \
                _join_vi([_DAY_LABELS[d] for d in sorted(days) if d in _DAY_LABELS], last="hoặc")
            out.append(f"rảnh {joined}")

    a_from, a_to = getattr(f, "available_from", None), getattr(f, "available_to", None)
    if a_from and a_to:
        out.append(f"trong khung {a_from}–{a_to}")

    if ctx.context.city:
        out.append(f"khu vực {ctx.context.city}")

    return out


def _preference_note(ctx, ai_ranked: bool) -> str:
    """Tiêu chí mềm chỉ ẢNH HƯỞNG THỨ TỰ, không lọc ai ra — nên chỉ được nói "ưu tiên".
    Và khi ranking degrade (ai_ranked=False) thì .NET đã BỎ query, lúc đó nói "ưu tiên"
    cũng là hứa hão."""
    prefs = (getattr(ctx.filters, "preferences", None) or "").strip()
    if not prefs or not ai_ranked:
        return ""
    return f" Mình ưu tiên {prefs}."


async def _found_reply(ctx, count: int, ai_ranked: bool) -> str:
    """Câu mở đầu khi CÓ kết quả. Dựng bằng code, không qua LLM: chỗ này là nơi duy nhất
    khẳng định 'đã lọc theo X' nên không được phép sai."""
    crit = await _applied_criteria(ctx)
    head = f"Mình tìm được {count} gia sư"
    if crit:
        head += " " + _join_vi(crit)
    head += ":"
    return head + _preference_note(ctx, ai_ranked)


async def _empty_reply(ctx) -> str:
    """0 kết quả: nêu RÕ đang lọc theo gì. Câu chung chung 'thử nới bớt yêu cầu' bắt user
    tự đoán tiêu chí nào đang chặn — mà tiêu chí đó có thể do họ nói từ 5 lượt trước."""
    crit = await _applied_criteria(ctx)
    if not crit:
        return ("Tiếc là chưa có gia sư nào khớp tiêu chí này. Bạn thử nới bớt yêu cầu "
                "(giá, môn, khu vực…) nhé?")
    return (f"Mình chưa tìm được gia sư {_join_vi(crit)}. "
            "Bạn nới bớt một tiêu chí (giá, lịch học, khu vực…) để mình tìm lại nhé?")


# Chênh lệch similarity tối thiểu để dám gắn nhãn "PHÙ HỢP NHẤT". Dưới ngưỡng này thì
# thứ nhất và thứ nhì chỉ hơn nhau trong sai số — đổi cách diễn đạt câu hỏi là đảo ngôi.
_BEST_MATCH_MIN_GAP = 0.02


def _has_clear_best(tutors: list[dict], ai_ranked: bool) -> bool:
    """Có người khớp VƯỢT TRỘI thật không.

    Nhãn "PHÙ HỢP NHẤT" là một KHẲNG ĐỊNH, không phải nhãn trang trí cho ô đầu tiên.
    Gắn nó lên người chỉ hơn nửa vời (hoặc hơn nhờ rating chứ không nhờ khớp hồ sơ) là
    lặp lại đúng lỗi "nói một đằng lọc một nẻo" — user mở hồ sơ ra thấy chẳng liên quan gì
    tới tiêu chí mình vừa nêu.
    """
    if not ai_ranked or not tutors:
        return False
    if len(tutors) == 1:
        return True
    sims = [t.get("aiSimilarity") for t in tutors[:2]]
    if any(x is None for x in sims):
        return False
    return (sims[0] - sims[1]) >= _BEST_MATCH_MIN_GAP


# Cụm chỉ dùng để LỌC CỨNG — đã thành filter rồi thì để lại trong query xếp hạng chỉ làm
# loãng tín hiệu phân biệt. Chính comment trong tutor_embed.py đã đo được hiện tượng này:
# nhắc lại môn/lớp khiến "ai cũng giống ai" và DÌM mất đặc điểm riêng (bằng cấp, trường).
_FILTER_NOISE_RE = re.compile(
    r"(?:lớp|lop|khối|khoi)\s*\d{1,2}"                       # "lớp 12"
    r"|(?:thứ|thu)\s*\d|\bt[2-7]\b|\bcn\b|chủ nhật|chu nhat"   # thứ trong tuần
    r"|cuối tuần|cuoi tuan|trong tuần|trong tuan"
    r"|(?:dưới|duoi|trên|tren|từ|tu|khoảng|khoang)?\s*\d+\s*(?:k|nghìn|nghin|tr|triệu|trieu|đ|vnd)\b"                     # "dưới 200k"
    r"|\d{1,2}\s*(?:h|giờ|gio)\b"                              # "19h", "7 giờ"
    r"|rảnh được|ranh duoc|rảnh|ranh|có thể dạy|co the day"
    r"|(?:tôi|toi|mình|minh|em)\s*(?:muốn|muon|cần|can)?\s*tìm gia sư"
    r"|tìm gia sư|tim gia su|gia sư|gia su",
    re.IGNORECASE,
)


def _semantic_part(message: str) -> str:
    """Bỏ khỏi tin nhắn những cụm ĐÃ thành filter cứng, giữ lại phần mô tả mong muốn.

    Vì sao không dùng thẳng bản `preferences` do LLM trích: nó diễn giải lại lời user và
    mỗi lần một kiểu ("ở UK hoặc Trung Quốc" / "ở nước ngoài (UK, Trung Quốc)") → cùng câu
    hỏi cho ra thứ hạng khác nhau. Cắt bằng luật thì tất định: cùng input, cùng output.
    """
    out = _FILTER_NOISE_RE.sub(" ", message or "")
    # Dọn phần thừa sau khi cắt: liên từ mồ côi ("... và , ...") và dấu phẩy dính nhau.
    out = re.sub(r"(?:^|[,;])\s*(?:và|va|hoặc|hoac|với|voi)\s*(?=[,;]|$)", " ", out,
                 flags=re.IGNORECASE)
    out = re.sub(r"\s*[,;]\s*(?=[,;])", " ", out)
    out = re.sub(r"\s{2,}", " ", out).strip(" ,;.")
    # Cắt sạch quá (câu chỉ toàn tiêu chí cứng) → giữ nguyên bản gốc, thà nhiễu còn hơn rỗng.
    return out if len(out) >= 12 else (message or "").strip()


def _rank_query(ctx) -> str:
    """Query cho xếp hạng = NGUYÊN VĂN tin nhắn hiện tại + mong muốn của các lượt TRƯỚC.

    KHÔNG dùng filters.preferences (đã gộp phần router vừa trích từ chính tin nhắn này):
    đó là bản LLM DIỄN GIẢI LẠI lời user, và mỗi lần gọi lại diễn giải một kiểu
    ("ở UK hoặc Trung Quốc" / "ở nước ngoài (UK, Trung Quốc)"). Ghép nó vào query khiến
    embedding đổi theo, cùng một câu hỏi cho ra thứ hạng khác nhau giữa các lần chạy —
    đo được: 3 lần chạy cùng câu, 2 lần ra người khớp nhất, 1 lần người đó văng khỏi top.
    Lời user thì cố định, nên lấy thẳng lời user.
    """
    msg = _semantic_part(ctx.message)
    prefs = (getattr(ctx, "prior_preferences", None) or "").strip()
    if not prefs:
        return msg
    if not msg:
        return prefs
    keep = [p for p in (x.strip() for x in prefs.split(",")) if p and p.lower() not in msg.lower()]
    return f"{msg}. {', '.join(keep)}" if keep else msg


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
        # "còn ai khác không" — cùng tiêu chí, khác NGƯỜI. Không loại thì lượt sau bắn lại
        # đúng 2 người vừa gợi ý (.NET /recommend không có tham số exclude, lọc phía client).
        wants_more = _wants_more(ctx.message)
        exclude = _exclude_ids(ctx, wants_more)
        # Ghi lại vào filters để lượt sau còn biết đã giới thiệu ai (FE giữ hộ, .NET chuyển
        # nguyên khối). Câu tìm kiếm bình thường → exclude rỗng → xoá luôn state cũ.
        ctx.filters.exclude_tutor_ids = exclude or None

        # Query cho xếp hạng ngữ nghĩa = tin nhắn HIỆN TẠI + mong muốn TÍCH LUỸ. Chỉ gửi
        # mỗi tin nhắn hiện tại (bản cũ) thì "con mất gốc" nêu ở lượt 1 không còn ảnh hưởng
        # gì tới thứ tự ở lượt 3 — user tưởng đã nói rồi, hệ thống thì quên sạch.
        query = _rank_query(ctx)
        try:
            content = await _fetch_candidates(
                ctx.context, ctx.filters, query, extra_top_k=len(exclude))
            tutors = content.get("tutors", []) or []
            ai_ranked = bool(content.get("aiRanked", False))
            # Thứ tự do Ranking Core quyết (Bayesian + blend). Chỉ khi core fail (SQL order)
            # mới hạ gia sư 0-review xuống cuối — giống luồng Zalo, giữ nhất quán ranking.
            if not ai_ranked:
                tutors.sort(key=lambda t: (t.get("totalReviews") or 0) == 0)
            if exclude:
                tutors = [t for t in tutors if t.get("tutorId") not in exclude]
        except Exception as e:
            print(f"web tutor handler error: {e}")
            return WebChatResponse(
                reply="Xin lỗi, mình chưa tải được danh sách gia sư. Bạn thử lại giúp mình nhé.",
                intent="tutor", filters=ctx.filters,
            )

        shown = tutors[:_MAX_CARDS]
        best = _has_clear_best(tutors, ai_ranked)
        cards = [_to_card(t, is_best=(i == 0 and best)) for i, t in enumerate(shown)]

        # Log có cấu trúc cho MỌI lượt search. Hai thứ cần đo:
        #   - lượt 0 kết quả + filter kèm theo → thấy tiêu chí nào đang chặn quá tay;
        #   - câu user kèm filter trích được → thấy yêu cầu nào KHÔNG map được vào filter
        #     nào (vd "dạy được 3 buổi/tuần", "gần nhà tôi") → biết trục nào đáng thêm,
        #     thay vì đợi user phàn nàn rồi mới đoán.
        # ensure_ascii mac dinh (True): console Windows chay cp1258, in thang tieng Viet
        # la UnicodeEncodeError -> sap nguyen request (da dinh khi test that). Log KHONG
        # DUOC PHEP lam hong luong chinh; escape uXXXX van la JSON hop le, json.loads ra
        # lai tieng Viet binh thuong.
        print("web_tutor_search " + json.dumps({
            "message": (ctx.message or "")[:200],
            "filters": ctx.filters.model_dump(exclude_none=True),
            "total": len(tutors), "shown": len(cards), "ai_ranked": ai_ranked,
        }))

        # Ranking degrade (.NET không gọi được AI rank) → `query` bị BỎ, kết quả chỉ là
        # SQL lọc cứng. Nếu user có nêu mong muốn ngữ nghĩa (bằng cấp, kinh nghiệm...) thì
        # câu router sinh dễ khẳng định sai → thay bằng câu trung thực, không hứa hão.
        if cards and not ai_ranked:
            reply = (
                await _found_reply(ctx, len(cards), ai_ranked=False) +
                " Hiện mình chưa xếp hạng được theo các mong muốn chi tiết (bằng cấp, "
                "kinh nghiệm…), bạn xem thử rồi cho mình biết để lọc kỹ hơn nhé."
            )
            return WebChatResponse(
                reply=reply, intent="tutor", cards=cards,
                filters=ctx.filters, ai_ranked=False, suggestions=ctx.suggestions,
            )

        # Reply: KHÔNG dùng câu router sinh cho nhánh có kết quả. Router viết câu dựa trên
        # TIN NHẮN của user, nên nó khẳng định theo ý user muốn chứ không theo cái đã lọc —
        # đó là đường dẫn tới "nói một đằng lọc một nẻo" (xem _applied_criteria).
        reply = ""
        if not cards and wants_more:
            # Hết người CHƯA giới thiệu ≠ không có ai khớp. Nói nhầm vế thứ hai là phủ nhận
            # luôn những gia sư vừa gợi ý ở ngay trên, user sẽ tưởng bot mâu thuẫn.
            reply = (
                "Mình đã giới thiệu hết các gia sư khớp tiêu chí này rồi ạ. Bạn nới bớt "
                "yêu cầu (giá, lịch học, khu vực…) để mình tìm thêm nhé?"
            )
        elif not cards:
            reply = await _empty_reply(ctx)
        else:
            reply = await _found_reply(ctx, len(cards), ai_ranked)
            # Cho xem kết quả TRƯỚC rồi mới gợi mở thêm — KHÔNG chặn lại phỏng vấn trước khi
            # cho xem gì. Hỏi đúng 1 tiêu chí user CHƯA nêu; trả lời hay không đều được, kết
            # quả đã có sẵn ở trên. LLM diễn đạt để câu hỏi tự nhiên và đổi theo ngữ cảnh,
            # thay vì ghép chuỗi cứng nghe như biểu mẫu.
            follow_up = _follow_up_question(ctx, reply)
            if follow_up:
                reply = f"{reply.rstrip()} {follow_up}"

        return WebChatResponse(
            reply=reply, intent="tutor", cards=cards,
            filters=ctx.filters, ai_ranked=ai_ranked, suggestions=ctx.suggestions,
        )
