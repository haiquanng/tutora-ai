"""
Agent hội thoại Tutora — kiến trúc SLOT-FILLING (dùng chung Zalo sale + Web).

VÌ SAO SLOT-FILLING (đọc trước khi sửa):
- Bản cũ để LLM (flash-lite) tự chọn tool qua function-calling + một loạt "gate hy vọng"
  và "force config" để vá tật model. Kết quả: model hay bịa gia sư, hỏi lại điều đã biết,
  im lặng. Mỗi bản vá đẻ lỗi mới.
- Bản này TÁCH 2 TẦNG rõ ràng:
    (1) CODE deterministic giữ STATE (slot) + quyết định "khi nào hỏi / khi nào search /
        khi nào confirm". Đây là logic nghiệp vụ — KHÔNG phó mặc prompt.
    (2) LLM chỉ làm 2 việc nó giỏi: TRÍCH slot/intent từ ngôn ngữ tự do, và DIỄN ĐẠT
        tiếng Việt tự nhiên cho bước code đã chọn. LLM không tự quyết nghiệp vụ/tiền bạc.

SLOT (state, NestJS persist qua context_patch, gửi lại mỗi lượt — stateless như tutor_chat):
    subject (môn) · grade (lớp) · goal (mục tiêu học) · preferences (mong muốn gia sư).
Đủ subject + grade + goal → được phép search. Thiếu → hỏi đúng cái thiếu.

BOOKING/PAYMENT: KHÔNG trong agent. Ý định đặt lịch → confirm → handoff_to_booking cho
NestJS xử lý deterministic. Tiền là nhị phân đúng/sai, không để LLM quyết.
"""
from __future__ import annotations

import asyncio
import json
import re

import httpx
from google import genai
from google.genai import types
from google.genai import errors as genai_errors

from ..core.config import get_settings
from ..core.dependencies import get_gemini_client, get_supabase
from ..models.schemas import AgentRequest, AgentResponse, AgentContextPatch, TutorChatFilters
from .tutor_chat import _fetch_candidates, _get_subjects
from .rag import retrieve_chunks

_settings = get_settings()

# gemini-2.5-flash: ổn định hơn hẳn lite ở trích JSON + hiểu ngữ cảnh tiếng Việt.
# Luồng sale đáng tiền — bớt bịa/hỏi lại hơn nhiều so với flash-lite.
_MODEL = "gemini-2.5-flash"
# PHẢI khớp MAX_CARDS bên NestJS (agent.handler) — số card gia sư thực render trên Zalo.
# 3 = top 3 chia 3 tier Standard/Pro/Premium theo user flow chính thức (agents/agentscenarios.md KB-A).
_MAX_CARDS_SHOWN = 3

# Retry lỗi TẠM THỜI của Gemini (503 quá tải / 429 / timeout). Backoff tăng dần.
_RETRY_DELAYS = [0.8, 2.0]
_RETRYABLE = (genai_errors.ServerError, genai_errors.APIError)


async def _generate(contents, config):
    """generate_content (sync) trong thread + retry lỗi tạm thời của Gemini."""
    gemini = get_gemini_client()
    last_exc = None
    for attempt in range(len(_RETRY_DELAYS) + 1):
        try:
            return await asyncio.to_thread(
                gemini.models.generate_content, model=_MODEL, contents=contents, config=config,
            )
        except _RETRYABLE as e:
            code = getattr(e, "code", None)
            if code is not None and code < 500 and code != 429:
                raise
            last_exc = e
            if attempt < len(_RETRY_DELAYS):
                await asyncio.sleep(_RETRY_DELAYS[attempt])
    raise last_exc


# ───────────────────────── LÀM SẠCH TIN NHẮN (Zalo) ─────────────────────────
# Zalo không render markdown; không được lộ id kỹ thuật. Persona đã dặn, strip thêm 1 lớp.
_ID_LEAK_PAREN_RE = re.compile(r"\s*[\(\[]\s*id\s*[:=]?\s*[\w-]+\s*[\)\]]", re.IGNORECASE)
_ID_LEAK_BARE_RE = re.compile(r"\bid\s*[:=]?\s*[\w-]{3,}\s*", re.IGNORECASE)
_ID_TOKEN_RE = re.compile(
    r"[\"'“”]?\b(?:seed-[\w-]+|[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})\b[\"'“”]?",
    re.IGNORECASE,
)

# ── Phát hiện id kỹ thuật TRONG TIN NHẮN USER (khác _ID_TOKEN_RE ở trên — cái đó lọc OUTPUT) ──
# User thường không thấy/không biết id thật (tutor-xxx, uuid) → gõ id là dev test hoặc trêu,
# KHÔNG phải nhu cầu thật. Xem agents/agentscenarios.md mục KB-B/KB-F.
_TECH_ID_INPUT_RE = re.compile(
    r"\b(?:tutor|seed)[-_][\w-]+\b"
    r"|\b[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\b",
    re.IGNORECASE,
)


def _sanitize_reply(text: str) -> str:
    if not text:
        return text
    text = _ID_LEAK_PAREN_RE.sub("", text)
    text = _ID_LEAK_BARE_RE.sub("", text)
    text = _ID_TOKEN_RE.sub("", text)
    text = re.sub(r"\*\*([^*]+)\*\*", r"\1", text)
    text = re.sub(r"\*([^*]+)\*", r"\1", text)
    text = text.replace("`", "")
    text = re.sub(r"(?m)^\s*[#>]+\s*", "", text)
    text = re.sub(r"(?m)^\s*[-*]\s+", "", text)
    text = re.sub(r"[ \t]{2,}", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


# ───────────────────────── GIỌNG / STYLE cho phần DIỄN ĐẠT ─────────────────────────
_STYLE = (
    "Em là trợ lý của Tutora, giúp phụ huynh tìm gia sư cho con. Xưng 'em', gọi phụ huynh "
    "'anh/chị'. Lễ phép, thân thiện, tự nhiên như người Việt tư vấn thật; có 'dạ', 'ạ' đúng "
    "mực (đừng lạm dụng). NGẮN GỌN 1-2 câu, tiếng Việt có dấu, tránh dịch máy. "
    "Zalo KHÔNG render markdown: TUYỆT ĐỐI không dùng '**', '*', '#', '`', gạch đầu dòng, "
    "không in đậm/nghiêng. Không để lộ id kỹ thuật — chỉ gọi gia sư bằng TÊN."
)


# ───────────────────────── (1) TRÍCH SLOT + INTENT ─────────────────────────
# 1 LLM call → JSON. Đây là chỗ DUY NHẤT LLM "hiểu ý" phụ huynh. Không function-calling,
# không để LLM quyết hành động — chỉ rút thông tin, code quyết ở bước sau.
_INTENT_VALUES = [
    "find_tutor",       # muốn tìm/xem gia sư (hoặc cung cấp thêm nhu cầu để tìm)
    "tutor_detail",     # hỏi sâu về MỘT gia sư đã gợi ý
    "availability",     # hỏi lịch rảnh/giá 1 gia sư đã gợi ý
    "faq",              # hỏi về Tutora (chính sách, cách hoạt động, giá chung)
    "booking",          # muốn đặt lịch / đăng ký học 1 gia sư
    "change_context",   # đổi môn / đổi lớp / đổi bé / đổi mục tiêu (cần confirm)
    "chitchat",         # chào hỏi / lạc đề / xác nhận ngắn ('ok','được')
]


def _extract_config(subjects_hint: str, slots: dict, shown_hint: str) -> types.GenerateContentConfig:
    known = json.dumps({k: v for k, v in slots.items() if v}, ensure_ascii=False)
    instruction = (
        "Bạn là bộ TRÍCH THÔNG TIN cho trợ lý tìm gia sư Tutora. Đọc lịch sử hội thoại + tin "
        "nhắn mới của phụ huynh, trả về DUY NHẤT một JSON (không giải thích, không markdown) "
        "theo schema:\n"
        '{"intent": <một trong ' + str(_INTENT_VALUES) + ">, "
        '"subject": <tên môn nếu phụ huynh nêu/đổi, vd "Toán","Tiếng Anh","Ngữ văn"; null nếu không nhắc>, '
        '"grade": <số lớp 1-12 nếu nêu/đổi; null nếu không nhắc>, '
        '"goal": <mục tiêu học 1 cụm ngắn nếu nêu, vd "mất gốc","củng cố","nâng cao","ôn thi chuyển cấp","luyện SAT phần Toán"; null nếu không nhắc>, '
        '"preferences": <mong muốn về gia sư 1 cụm ngắn nếu nêu, vd "kiên nhẫn","nghiêm khắc","học online"; null nếu không nhắc>, '
        '"tutor_ref": <TÊN gia sư phụ huynh đang hỏi tới nếu có, lấy từ danh sách đã gợi ý; null nếu không>, '
        '"rush": <true nếu phụ huynh GIỤC xem gia sư ngay ("đưa tôi gia sư","có ai không","xem luôn đi","nhanh lên"); false nếu bình thường>}\n\n'
        "QUY TẮC QUAN TRỌNG:\n"
        "- Ưu tiên đọc TIN NHẮN MỚI NHẤT của phụ huynh. Nếu tin nhắn mới có nêu môn/lớp/mục "
        "tiêu/mong muốn thì PHẢI điền field tương ứng, KỂ CẢ khi nhiều thứ nằm chung 1 câu.\n"
        "- BẮT LỚP RẤT KỸ: bất cứ khi nào có 'lớp <số>' hoặc 'con/bé lớp <số>' trong tin nhắn "
        "mới → grade = <số> đó. Vd 'gia sư toán lớp 9 ôn thi' → subject='Toán', grade=9, "
        "goal='ôn thi'. TUYỆT ĐỐI đừng bỏ sót lớp khi nó đứng chung câu với môn.\n"
        "- CHỈ điền field khi phụ huynh THỰC SỰ nêu. KHÔNG bịa, KHÔNG suy diễn. Không nhắc = null.\n"
        "- Đã biết sẵn từ các lượt trước (nếu tin nhắn mới KHÔNG nhắc lại và KHÔNG đổi thì để "
        "null, hệ thống tự giữ giá trị cũ — đừng chép lại): " + known + ".\n"
        "- MỤC TIÊU HIỂU RỘNG: 'luyện thi SAT/IELTS/TOEIC', 'thi HSG', 'ôn thi chuyển cấp', "
        "'thi vào 10', 'thi THPTQG' đều là GOAL (mục tiêu), KHÔNG phải môn học. Vd 'luyện thi "
        "SAT' → goal='luyện thi SAT' (KHÔNG phải subject). Nếu SAT/IELTS mà chưa rõ môn thì để "
        "subject=null (bước sau sẽ hỏi).\n"
        "- intent='tutor_detail'/'availability'/'booking' chỉ khi có gia sư đã gợi ý trước đó. "
        "Danh sách gia sư đã gợi ý: " + (shown_hint or "chưa có") + ".\n"
        "- XÁC NHẬN ĐỔI MÔN/LỚP (quan trọng): nếu tin nhắn TRƯỚC của trợ lý là câu hỏi xác "
        "nhận đổi sang môn/lớp cụ thể (vd 'anh/chị muốn tìm gia sư Tiếng Anh, đúng không ạ?') "
        "thì:\n"
        "    · PH ĐỒNG Ý ('đúng rồi','đúng','ừ','ok','vâng','phải') → intent='find_tutor' VÀ "
        "điền subject/grade = ĐÚNG môn/lớp mà trợ lý vừa hỏi xác nhận (đọc từ câu hỏi đó).\n"
        "    · PH TỪ CHỐI ('không','giữ như cũ','thôi') → intent='chitchat', subject=null, "
        "grade=null (giữ nguyên môn/lớp cũ, KHÔNG đổi).\n"
        "- Câu ngắn xác nhận ('ok','được','có','đúng rồi') sau khi trợ lý hỏi chuyện KHÁC (không "
        "phải đổi môn/lớp) → intent='chitchat'.\n"
        "- Phụ huynh giục xem gia sư ('đưa tôi gia sư','có ai không','xem gia sư') → intent='find_tutor' và rush=true.\n"
        "- Nếu tin nhắn TRƯỚC của trợ lý hỏi về mong muốn thêm/khu vực/hình thức học mà phụ "
        "huynh trả lời KHÔNG có yêu cầu ('sao cũng được','không có gì','gì cũng được','tùy em', "
        "kể cả 'ừ','ok','được' ngay sau câu hỏi đó — quy tắc này ƯU TIÊN hơn quy tắc câu ngắn "
        "xác nhận ở trên) → intent='find_tutor', preferences='không có yêu cầu đặc biệt' (để hệ "
        "thống biết đã hỏi xong, tìm luôn).\n"
        "Môn Tutora có: " + subjects_hint + "."
    )
    return types.GenerateContentConfig(
        system_instruction=instruction,
        temperature=0.1,
        response_mime_type="application/json",
    )


async def _extract_turn(history_contents: list, message: str, slots: dict,
                        subjects_hint: str, shown_hint: str) -> dict:
    """Trả {intent, subject, grade, goal, preferences, tutor_ref}. Fallback an toàn nếu lỗi."""
    contents = list(history_contents)
    contents.append(types.Content(role="user", parts=[types.Part.from_text(text=message)]))
    try:
        resp = await _generate(contents, _extract_config(subjects_hint, slots, shown_hint))
        data = json.loads((resp.text or "{}").strip())
    except Exception as e:
        print(f"agent _extract_turn error: {e}")
        # Không hiểu được → coi như muốn tìm gia sư, để luồng hỏi tiếp (an toàn).
        # Trả ĐỦ key (code sau truy cập ex["subject"]... trực tiếp — thiếu key là KeyError).
        return {"intent": "find_tutor", "subject": None, "grade": None, "goal": None,
                "preferences": None, "tutor_ref": None, "rush": False}
    intent = data.get("intent")
    if intent not in _INTENT_VALUES:
        intent = "find_tutor"
    return {
        "intent": intent,
        "subject": (data.get("subject") or None),
        "grade": data.get("grade") if isinstance(data.get("grade"), int) else None,
        "goal": (data.get("goal") or None),
        "preferences": (data.get("preferences") or None),
        "tutor_ref": (data.get("tutor_ref") or None),
        "rush": bool(data.get("rush")),
    }


# ───────────────────────── DIỄN ĐẠT (LLM sinh câu chữ 1 lượt) ─────────────────────────
async def _say(task: str, history_contents: list | None = None) -> str:
    """LLM sinh 1 câu tiếng Việt theo yêu cầu 'task'. Dùng cho hỏi thêm / giới thiệu / báo lỗi.
    task đã chứa đủ dữ kiện; LLM chỉ diễn đạt, không tự bịa thêm thông tin."""
    config = types.GenerateContentConfig(system_instruction=_STYLE, temperature=0.4)
    contents = list(history_contents or [])
    contents.append(types.Content(role="user", parts=[types.Part.from_text(text=task)]))
    try:
        resp = await _generate(contents, config)
        return _sanitize_reply((resp.text or "").strip())
    except Exception as e:
        print(f"agent _say error: {e}")
        return ""


# ───────────────────────── MAP TÊN → ID (.NET) ─────────────────────────
async def _resolve_subject_id(subject_name: str | None) -> int | None:
    if not subject_name:
        return None
    subjects = await _get_subjects()
    norm = subject_name.strip().lower()
    for s in subjects:
        if (s.get("subjectName") or "").strip().lower() == norm:
            return s.get("subjectId")
    for s in subjects:
        name = (s.get("subjectName") or "").strip().lower()
        if norm in name or name in norm:
            return s.get("subjectId")
    return None


_grade_levels_cache: list[dict] = []


async def _get_grade_levels() -> list[dict]:
    """[{gradeLevelId, gradeName, levelOrder}] từ .NET; cache. id KHÔNG tuần tự theo lớp."""
    global _grade_levels_cache
    if _grade_levels_cache:
        return _grade_levels_cache
    try:
        url = f"{_settings.dotnet_be_url}/api/grade-levels"
        async with httpx.AsyncClient(timeout=15) as client:
            r = await client.get(url, headers={"Accept": "application/json"})
            r.raise_for_status()
            _grade_levels_cache = r.json().get("content", []) or []
    except Exception as e:
        print(f"agent grade-levels fetch error: {e}")
    return _grade_levels_cache


async def _resolve_grade_id(grade: int | None) -> int | None:
    if not grade:
        return None
    for g in await _get_grade_levels():
        if g.get("levelOrder") == grade:
            return g.get("gradeLevelId")
    return None


async def _dotnet_get(path: str) -> dict | None:
    url = f"{_settings.dotnet_be_url}{path}"
    try:
        async with httpx.AsyncClient(timeout=20) as client:
            r = await client.get(url, headers={"Accept": "application/json"})
            r.raise_for_status()
            data = r.json()
        return data.get("content", data)
    except Exception as e:
        print(f"agent _dotnet_get {path} error: {e}")
        return None


# ───────────────────────── SEARCH GIA SƯ (deterministic) ─────────────────────────
async def _run_search(ctx, query: str) -> tuple[list, list]:
    """Gọi Ranking Core (.NET /recommend). Trả (full_list_để_render, shown_summary_cho_LLM)."""
    filters = TutorChatFilters(subject_id=ctx.subject_id)
    try:
        content = await _fetch_candidates(ctx, filters, query=query)
        tutors = content.get("tutors", []) or []
        # Thứ tự do Ranking Core; chỉ khi core fail mới hạ gia sư 0-review xuống cuối.
        if not content.get("aiRanked"):
            tutors.sort(key=lambda t: (t.get("totalReviews") or 0) == 0)
    except Exception as e:
        print(f"agent _run_search error: {e}")
        return [], []
    shown = tutors[:_MAX_CARDS_SHOWN]
    summary = [
        {"name": t.get("fullName") or t.get("name"),
         "rating": t.get("averageRating"),
         # .NET TutorRecommendItem trả pricePerHour (không phải hourlyRate/priceMin).
         "rate": t.get("pricePerHour") or t.get("hourlyRate") or t.get("priceMin")}
        for t in shown
    ]
    return tutors, summary


# ───────────────────────── AGENT (điều phối deterministic) ─────────────────────────
async def run_agent(body: AgentRequest) -> AgentResponse:
    ctx = body.context

    # Slot hiện có (từ context NestJS gửi kèm). subject_id/grade_level_id là id thật;
    # goal/preferences là text. subject/grade "đọc được" (tên/số) suy ra khi cần hỏi.
    slots = {
        "subject_id": ctx.subject_id,
        "grade_level_id": ctx.grade_level_id,
        "goal": ctx.goal,
        "preferences": ctx.preferences,
    }

    # History → Content list (tái dùng cho cả trích slot lẫn diễn đạt).
    history_contents: list[types.Content] = [
        types.Content(role=("user" if m.role == "user" else "model"),
                      parts=[types.Part.from_text(text=m.content)])
        for m in body.history
    ]

    # ── Chặn id kỹ thuật ở tầng CODE (dev test / user trêu, KHÔNG phải nhu cầu thật) ──
    # Bắt TRƯỚC khi gọi LLM trích slot: không tốn lượt gọi, không để LLM "hiểu nhầm" thành
    # tutor_detail rồi lộ hành vi lấy nhầm gia sư. Xem agents/agentscenarios.md KB-B/KB-F.
    if _TECH_ID_INPUT_RE.search(body.message):
        r = await _say(
            "Phụ huynh gõ một mã/id kỹ thuật (không phải nhu cầu thật — nhiều khả năng là dev "
            "đang test hoặc trêu, vì phụ huynh thật không biết/không thấy id này). KHÔNG xác nhận "
            "hay tra cứu theo id đó. Đáp gọn, lịch sự, mời anh/chị cho biết TÊN gia sư hoặc nhu "
            "cầu tìm gia sư để được hỗ trợ.", history_contents)
        return AgentResponse(
            reply=r or "Dạ anh/chị cho em biết tên gia sư hoặc nhu cầu để em hỗ trợ ạ.")

    subjects = await _get_subjects()
    subjects_hint = ", ".join(s.get("subjectName", "") for s in subjects)
    # Tên môn/số lớp hiện tại (để nhắc LLM biết đã có gì, và để dựng câu hỏi tự nhiên).
    cur_subject_name = next(
        (s.get("subjectName") for s in subjects if s.get("subjectId") == ctx.subject_id), None)
    cur_grade = None
    if ctx.grade_level_id is not None:
        for g in await _get_grade_levels():
            if g.get("gradeLevelId") == ctx.grade_level_id:
                cur_grade = g.get("levelOrder")
                break

    allowed = {t.tutor_id: (t.name or "") for t in body.shown_tutors}
    shown_hint = "; ".join(n for n in allowed.values() if n) if allowed else ""

    slots_readable = {"subject": cur_subject_name, "grade": cur_grade,
                      "goal": ctx.goal, "preferences": ctx.preferences}

    # ── (1) TRÍCH slot + intent ──
    ex = await _extract_turn(history_contents, body.message, slots_readable, subjects_hint, shown_hint)

    patch_out: dict = {}

    def _patch():
        return AgentContextPatch(**patch_out) if patch_out else None

    # ── Phát hiện ĐỔI môn/lớp (cần confirm trước khi tìm lại) ──
    # Nếu môn/lớp mới KHÁC môn/lớp đang có VÀ đã từng gợi ý gia sư → đây là đổi ngữ cảnh,
    # phải hỏi xác nhận (tránh tìm nhầm khi PH lỡ tay). Bắt ở tầng code cho chắc — extract
    # hay phân "cho tôi gia sư Anh thay vì Toán" thành find_tutor. CHƯA áp patch ở nhánh này.
    new_sid = await _resolve_subject_id(ex["subject"]) if ex["subject"] else None
    new_gid = await _resolve_grade_id(ex["grade"]) if ex["grade"] else None
    switching_subject = new_sid is not None and ctx.subject_id is not None and new_sid != ctx.subject_id
    switching_grade = new_gid is not None and ctx.grade_level_id is not None and new_gid != ctx.grade_level_id
    # Lượt này CÓ PHẢI câu trả lời xác nhận đổi ngữ cảnh không? Nếu tin nhắn model gần nhất
    # là câu hỏi xác nhận ('đúng không ạ') → PH đang trả lời confirm, KHÔNG hỏi lại lần nữa
    # (tránh vòng lặp confirm). Cho áp patch + search luôn ở dưới.
    last_model_msg = next((m.content for m in reversed(body.history) if m.role != "user"), "")
    answering_confirm = ("đúng không" in last_model_msg.lower()
                         or "đúng ko" in last_model_msg.lower())
    if (switching_subject or switching_grade) and body.shown_tutors and not answering_confirm:
        what = "môn" if switching_subject else "lớp"
        target = ex["subject"] if switching_subject else f"lớp {ex['grade']}"
        q = await _say(
            f"Phụ huynh muốn đổi {what} sang {target} (khác với đang tìm). Hỏi 1 câu xác nhận "
            "ngắn gọn, lịch sự rằng anh/chị muốn chuyển sang tìm gia sư mới cho lựa chọn này, "
            "đúng không ạ.", history_contents)
        # KHÔNG áp patch ở đây: nếu PH nói "không, giữ như cũ" thì môn/lớp cũ phải nguyên vẹn.
        # Lượt sau PH xác nhận ("đúng rồi") → extract rút lại môn/lớp mới → áp patch + search.
        return AgentResponse(
            reply=q or f"Dạ anh/chị muốn đổi sang {target} và tìm gia sư mới, đúng không ạ?",
            tutors=[], awaiting_confirmation=True, confirm_type="context_change",
            suggestions=["Đúng rồi", "Không, giữ như cũ"],
        )

    # ── Cập nhật slot từ thông tin mới rút được ──
    # subject/grade → map sang id + ghi patch để NestJS persist. goal/preferences → text.
    if new_sid is not None:
        ctx.subject_id = new_sid
        patch_out["subject_id"] = new_sid
        cur_subject_name = ex["subject"]
    if new_gid is not None:
        ctx.grade_level_id = new_gid
        patch_out["grade_level_id"] = new_gid
        cur_grade = ex["grade"]
    if ex["goal"]:
        ctx.goal = ex["goal"]
        patch_out["goal"] = ex["goal"]
    if ex["preferences"]:
        new_pref = ex["preferences"].strip()
        if "không có yêu cầu" in new_pref.lower():
            # PH từ chối lượt gộp tuỳ chọn ("sao cũng được") → đánh dấu ĐÃ HỎI XONG để gate
            # dưới search luôn. KHÔNG lưu làm preference thật (tránh nhiễu query search).
            ctx.asked_preferences = True
            patch_out["asked_preferences"] = True
        else:
            # Tích lũy mong muốn (nối, không đè) để không mất ý cũ — nhưng tránh nối trùng
            # (extract đôi khi lặp lại preference cũ từ history).
            old = ctx.preferences or ""
            if new_pref.lower() not in old.lower():
                merged = " ; ".join(x for x in [old, new_pref] if x)
                ctx.preferences = merged
                patch_out["preferences"] = merged

    intent = ex["intent"]

    # ── (2) ĐIỀU PHỐI theo intent (code quyết, không phải LLM) ──

    # FAQ: RAG. Rỗng → câu an toàn, KHÔNG bịa.
    if intent == "faq":
        return await _handle_faq(body.message, history_contents, _patch())

    # Hỏi chi tiết / lịch 1 gia sư đã gợi ý.
    if intent in ("tutor_detail", "availability"):
        return await _handle_tutor_query(intent, ex["tutor_ref"], allowed,
                                         history_contents, _patch())

    # Đổi ngữ cảnh (môn/lớp/bé/mục tiêu) → confirm trước khi tìm lại.
    if intent == "change_context":
        q = await _say(
            "Phụ huynh muốn đổi tiêu chí tìm gia sư (môn/lớp/mục tiêu). Hãy hỏi 1 câu xác nhận "
            "ngắn gọn, lịch sự, xác nhận thay đổi đó rồi mới tìm lại gia sư mới.",
            history_contents)
        return AgentResponse(
            reply=q or "Dạ anh/chị muốn đổi tiêu chí tìm gia sư, đúng không ạ?",
            awaiting_confirmation=True, confirm_type="context_change",
            suggestions=["Đúng rồi", "Không, giữ như cũ"], context_patch=_patch(),
        )

    # Đặt lịch → confirm → handoff booking (NestJS xử lý deterministic).
    if intent == "booking":
        q = await _say(
            "Phụ huynh muốn đặt lịch học với gia sư. Hãy hỏi 1 câu xác nhận ngắn gọn, lịch sự "
            "rằng anh/chị muốn đặt lịch, đúng không ạ.", history_contents)
        return AgentResponse(
            reply=q or "Dạ anh/chị muốn đặt lịch học với gia sư này, đúng không ạ?",
            awaiting_confirmation=True, confirm_type="booking", handoff_to_booking=True,
            suggestions=["Đúng, đặt lịch", "Chưa, xem thêm"], context_patch=_patch(),
        )

    # chitchat: chào / lạc đề / xác nhận ngắn → chào lại, gợi hỏi nhu cầu, KHÔNG search.
    if intent == "chitchat":
        r = await _say(
            "Phụ huynh gửi câu chào hoặc câu ngắn không rõ nhu cầu tìm gia sư. Chào lại thân "
            "thiện và hỏi anh/chị cần tìm gia sư môn gì, cho bé lớp mấy. KHÔNG nói 'chưa có thông tin'.",
            history_contents)
        return AgentResponse(reply=r or "Dạ em chào anh/chị ạ! Anh/chị cần tìm gia sư môn gì, "
                             "cho bé lớp mấy để em hỗ trợ ạ?", context_patch=_patch())

    # ── find_tutor: gate slot deterministic ──
    return await _handle_find_tutor(ctx, cur_subject_name, cur_grade, subjects_hint,
                                    body.message, history_contents, allowed, _patch, patch_out,
                                    rush=ex["rush"])


async def _handle_find_tutor(ctx, cur_subject_name, cur_grade, subjects_hint,
                             message, history_contents, allowed, patch_fn, patch_out,
                             rush: bool = False) -> AgentResponse:
    """Gate slot (subject+grade+goal) rồi search THẬT. Thiếu slot → hỏi đúng cái thiếu.
    rush=True (PH giục) → bỏ qua câu hỏi mềm (goal, lượt gộp tuỳ chọn), search ngay với slot
    hiện có — tôn trọng sự sốt ruột hơn thu đủ dữ liệu (KB-A). subject/grade vẫn bắt buộc."""
    # Thiếu môn → hỏi môn (kèm gợi ý map nếu là mục tiêu SAT/IELTS chưa rõ môn).
    if ctx.subject_id is None:
        # Nếu ĐÃ biết mục tiêu (vd 'luyện thi SAT') mà chưa rõ môn → gợi ý map mục tiêu về
        # môn cụ thể ngay trong câu hỏi, tránh hỏi trống 'muốn môn gì' lặp lại vô duyên.
        goal_hint = ""
        if ctx.goal:
            goal_hint = (
                f"Phụ huynh đã nêu mục tiêu: '{ctx.goal}'. Đây là MỤC TIÊU, không phải môn — "
                "Tutora tìm gia sư môn phổ thông để luyện mục tiêu đó (KHÔNG được nói Tutora "
                "không có chương trình này). Gợi ý cụ thể môn phù hợp rồi hỏi bé muốn học môn "
                "nào: SAT → phần Toán hoặc tiếng Anh; IELTS/TOEIC → tiếng Anh; thi HSG/chuyển "
                "cấp/THPTQG → hỏi bé cần môn nào. ")
        r = await _say(
            "Chưa biết phụ huynh muốn tìm gia sư MÔN gì. " + goal_hint +
            "Hỏi anh/chị cần tìm gia sư môn nào cho bé (1 câu, tự nhiên). "
            f"Các môn Tutora có: {subjects_hint}.", history_contents)
        return AgentResponse(reply=r or "Dạ anh/chị muốn tìm gia sư môn gì cho bé ạ?",
                             context_patch=patch_fn())

    # Thiếu lớp → hỏi lớp (LỚP là filter cứng, search thiếu lớp trả gia sư sai cấp).
    if ctx.grade_level_id is None:
        r = await _say(
            f"Đã biết môn cần tìm là {cur_subject_name or 'môn đã chọn'} nhưng CHƯA biết bé học "
            "lớp mấy. Hỏi anh/chị bé nhà mình đang học lớp mấy (chỉ hỏi lớp, đừng hỏi lại môn).",
            history_contents)
        return AgentResponse(reply=r or "Dạ bé nhà mình đang học lớp mấy ạ?", context_patch=patch_fn())

    # Thiếu mục tiêu → hỏi mục tiêu (1 câu, để tư vấn trúng thay vì bắn top-rating).
    # PH giục (rush) → bỏ qua, search luôn với slot hiện có (goal là câu hỏi mềm).
    if not ctx.goal and not rush:
        r = await _say(
            f"Đã biết cần gia sư {cur_subject_name or ''} lớp {cur_grade or ''}. Hỏi 1 câu ngắn "
            "về MỤC TIÊU học của bé để tư vấn trúng: bé cần mất gốc/củng cố lại, nâng cao, hay "
            "ôn thi (chuyển cấp/HSG/SAT...)? Chỉ hỏi mục tiêu, đừng hỏi lại môn/lớp.",
            history_contents)
        return AgentResponse(reply=r or "Dạ bé nhà mình học với mục tiêu gì ạ (củng cố, nâng cao "
                             "hay ôn thi)?", context_patch=patch_fn())

    # ── Lượt gộp tuỳ chọn "hỏi 1 lần, mềm" (KB-A bước 4 — agents/agentscenarios.md) ──
    # Đủ 3 slot bắt buộc nhưng chưa biết mong muốn thêm VÀ chưa từng hỏi → hỏi GỘP đúng 1 câu
    # (hình thức học/khu vực + mong muốn về gia sư). Đánh dấu asked_preferences qua patch để
    # lượt sau KHÔNG hỏi lại — PH trả lời gì (kể cả 'sao cũng được') lượt sau cũng search.
    # PH giục (rush) → bỏ qua luôn.
    if not rush and not ctx.preferences and not ctx.asked_preferences:
        patch_out["asked_preferences"] = True
        r = await _say(
            f"Đã đủ thông tin chính (gia sư {cur_subject_name or ''} lớp {cur_grade or ''}, mục "
            f"tiêu {ctx.goal}). Trước khi tìm, hỏi GỘP trong 1 câu duy nhất: anh/chị muốn bé học "
            "online hay gia sư đến nhà, và có mong muốn gì thêm về gia sư không (cô hay thầy, "
            "kiên nhẫn, nghiêm khắc...). Chốt câu bằng ý: không có thì em tìm luôn ạ. KHÔNG hỏi "
            "lại môn/lớp/mục tiêu.", history_contents)
        return AgentResponse(
            reply=r or "Dạ anh/chị muốn bé học online hay gia sư đến nhà ạ? Anh/chị có mong muốn "
            "gì thêm về gia sư không (cô hay thầy, kiên nhẫn...)? Không có thì em tìm luôn ạ!",
            context_patch=patch_fn())

    # ── Đủ slot → SEARCH THẬT ──
    query = ", ".join(x for x in [
        f"{cur_subject_name} lớp {cur_grade}" if cur_subject_name else None,
        ctx.goal, ctx.preferences,
    ] if x)
    tutors, shown = await _run_search(ctx, query)

    if not tutors:
        # Không có gia sư phù hợp → nói THẬT, KHÔNG bịa.
        r = await _say(
            f"Đã tìm nhưng CHƯA có gia sư {cur_subject_name or ''} lớp {cur_grade or ''} phù hợp "
            "với nhu cầu của phụ huynh trong hệ thống. Nói thật điều này một cách lịch sự, và gợi "
            "ý anh/chị thử nới tiêu chí hoặc để lại thông tin, em cập nhật sau. TUYỆT ĐỐI không "
            "bịa ra tên gia sư nào.", history_contents)
        return AgentResponse(
            reply=r or "Dạ hiện em chưa tìm được gia sư phù hợp với tiêu chí này ạ. Anh/chị thử "
            "nới tiêu chí giúp em nhé, hoặc em sẽ cập nhật khi có gia sư phù hợp ạ!",
            tutors=[], context_patch=patch_fn())

    # Có gia sư → LLM giới thiệu ĐÚNG những người trong 'shown' (không bịa, không nói tổng số).
    names_json = json.dumps(shown, ensure_ascii=False)
    r = await _say(
        "Đã tìm được gia sư phù hợp. Giới thiệu NGẮN GỌN cho phụ huynh đúng những gia sư trong "
        f"danh sách sau (chỉ dùng đúng các tên này, KHÔNG thêm ai khác, KHÔNG bịa): {names_json}. "
        "Mỗi người 1 dòng: tên + 1 lý do ngắn hợp với nhu cầu "
        f"({ctx.goal or 'học ' + (cur_subject_name or 'môn đã chọn')}"
        f"{'; ' + ctx.preferences if ctx.preferences else ''}). KHÔNG nói tổng số tìm "
        "được, KHÔNG liệt kê lại giá/đánh giá (đã có thẻ riêng bên dưới). Mời anh/chị xem thẻ chi tiết.",
        history_contents)
    return AgentResponse(
        reply=r or "Dạ em tìm được vài gia sư phù hợp, anh/chị xem thẻ chi tiết bên dưới giúp em nhé ạ!",
        tutors=tutors, context_patch=patch_fn())


async def _handle_faq(question: str, history_contents, patch) -> AgentResponse:
    """RAG trên KB Tutora. Rỗng → câu an toàn, chống bịa tuyệt đối."""
    try:
        chunks, _ = await retrieve_chunks(
            get_supabase(), None, question,
            gemini=get_gemini_client(), subject="tutora_kb", min_similarity=0.6,
        )
    except Exception as e:
        print(f"agent faq error: {e}")
        chunks = []
    passages = [c.get("content") or c.get("text") for c in chunks]
    passages = [p for p in passages if p]
    if not passages:
        return AgentResponse(
            reply="Dạ phần này em chưa có thông tin ạ. Anh/chị liên hệ hỗ trợ Tutora để được "
            "giải đáp chính xác giúp em nhé!", context_patch=patch)
    ctx_text = "\n".join(f"- {p}" for p in passages)
    r = await _say(
        f"Phụ huynh hỏi: \"{question}\". Trả lời DỰA HOÀN TOÀN vào thông tin sau, KHÔNG bịa "
        f"thêm ngoài đây:\n{ctx_text}\nNếu thông tin trên KHÔNG trả lời được câu hỏi, nói thật là "
        "phần này chưa có thông tin, mời anh/chị liên hệ hỗ trợ Tutora.", history_contents)
    return AgentResponse(reply=r or "Dạ anh/chị liên hệ hỗ trợ Tutora để được giải đáp giúp em nhé ạ!",
                         context_patch=patch)


async def _handle_tutor_query(intent: str, tutor_ref: str | None, allowed: dict,
                              history_contents, patch) -> AgentResponse:
    """Chi tiết / lịch 1 gia sư đã gợi ý. Chỉ cho phép gia sư trong danh sách đã shown."""
    if not allowed:
        r = await _say(
            "Phụ huynh hỏi chi tiết/lịch của một gia sư, nhưng em CHƯA gợi ý gia sư nào (chưa "
            "search). Mời anh/chị cho biết nhu cầu để em tìm gia sư trước đã.", history_contents)
        return AgentResponse(reply=r or "Dạ để em tìm gia sư phù hợp trước, anh/chị cho em biết "
                             "cần môn gì, bé lớp mấy ạ?", context_patch=patch)

    # Khớp tên phụ huynh nhắc → tutor_id.
    tid = None
    if tutor_ref:
        ref = tutor_ref.strip().lower()
        for i, name in allowed.items():
            if name and (ref in name.lower() or name.lower() in ref):
                tid = i
                break
    if tid is None:
        if len(allowed) == 1:
            # Chỉ có đúng 1 gia sư đã gợi ý → chắc chắn PH đang hỏi người đó.
            tid = next(iter(allowed))
        else:
            # Nhiều gia sư đã gợi ý mà tên không khớp ai → HỎI LẠI, KHÔNG đoán đại người đầu
            # (bug cũ: trả nhầm sang gia sư khác). Xem agents/agentscenarios.md KB-B.
            r = await _say(
                "Phụ huynh hỏi chi tiết/lịch một gia sư nhưng chưa rõ đang hỏi ai trong số các "
                "gia sư đã gợi ý. Hỏi lại lịch sự, ngắn gọn: anh/chị muốn xem gia sư nào ạ.",
                history_contents)
            return AgentResponse(reply=r or "Dạ anh/chị muốn xem gia sư nào ạ?", context_patch=patch)

    if intent == "tutor_detail":
        data = await _dotnet_get(f"/api/tutors/{tid}/full-profile")
        topic = "thông tin chi tiết (kinh nghiệm, học vấn, phong cách dạy)"
    else:
        data = await _dotnet_get(f"/api/tutors/{tid}/schedule")
        topic = "lịch rảnh / thời gian có thể dạy"
    tutor_name = allowed.get(tid) or "gia sư"

    if not data:
        return AgentResponse(
            reply=f"Dạ em chưa lấy được {topic} của {tutor_name} ngay lúc này ạ. Anh/chị chờ em "
            "chút hoặc thử lại giúp em nhé!", context_patch=patch)

    data_text = json.dumps(data, ensure_ascii=False)[:2500]
    r = await _say(
        f"Phụ huynh hỏi {topic} của gia sư {tutor_name}. Diễn đạt lại NGẮN GỌN, tự nhiên cho phụ "
        f"huynh dựa trên dữ liệu sau (KHÔNG bịa ngoài đây, KHÔNG lộ id/trường kỹ thuật):\n{data_text}",
        history_contents)
    return AgentResponse(
        reply=r or f"Dạ để em gửi anh/chị thông tin của {tutor_name} ạ.", context_patch=patch)
