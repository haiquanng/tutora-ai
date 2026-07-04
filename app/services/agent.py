"""
Agent hội thoại Tutora — MỘT core dùng chung cho Zalo (sale) và Web (đa năng).

KIẾN TRÚC (đọc trước khi sửa):
- LLM (Gemini) lo HỘI THOẠI + Ý ĐỊNH: hiểu phụ huynh, quyết định gọi tool nào,
  diễn đạt tiếng Việt. KHÔNG sinh quyết định nghiệp vụ/tiền bạc.
- TOOL lo phần DETERMINISTIC: search gia sư (Ranking Core), FAQ (RAG). Mỗi tool
  chỉ bọc code đã có sẵn — agent không "biết" cách tính ranking hay truy vấn DB.
- BOOKING/PAYMENT: KHÔNG nằm trong agent. Khi phụ huynh muốn đặt lịch, agent set
  cờ handoff_to_booking → NestJS chuyển sang deterministic booking flow. Tiền bạc
  là nhị phân đúng/sai → không để LLM tự quyết. (Xem nhóm tool GĐ2 ở cuối file.)

STATELESS: bên gọi (NestJS/Web) giữ history, gửi kèm mỗi request. Giống tutor_chat.

Đây là SKELETON để review cấu trúc. Phần đánh dấu [STUB] cần hoàn thiện.
"""
from __future__ import annotations

import asyncio
import re

import httpx
from google import genai
from google.genai import types
from google.genai import errors as genai_errors

from ..core.config import get_settings
from ..core.dependencies import get_gemini_client, get_supabase
from ..models.schemas import AgentRequest, AgentResponse, AgentContextPatch, TutorChatFilters
from .tutor_chat import _fetch_candidates, _get_subjects   # tái dùng: gọi .NET /recommend + /subjects
from .rag import retrieve_chunks

_settings = get_settings()

# gemini-2.5-flash-lite: rẻ nhất, function-calling đủ tốt cho luồng sale.
_MODEL = "gemini-2.5-flash-lite"
_MAX_TURNS = 5  # guard: chặn vòng lặp tool vô hạn (agent gọi tool mãi không trả lời).
# PHẢI khớp MAX_CARDS bên NestJS (zalo.handler) — số card gia sư thực render trên Zalo.
# LLM chỉ giới thiệu đúng bấy nhiêu người để câu chữ khớp số card hiển thị.
_MAX_CARDS_SHOWN = 2

# Retry khi Gemini lỗi TẠM THỜI (503 quá tải / 429 / timeout) — lỗi phía Google,
# không liên quan quota mình. Backoff tăng dần; hết retry -> raise để fallback graceful.
_RETRY_DELAYS = [0.8, 2.0]   # 2 lần retry (tổng 3 lần thử)
_RETRYABLE = (genai_errors.ServerError, genai_errors.APIError)


async def _generate_with_retry(gemini, contents, config):
    """Gọi generate_content (sync) trong thread + retry lỗi tạm thời của Gemini."""
    last_exc = None
    for attempt in range(len(_RETRY_DELAYS) + 1):
        try:
            # generate_content là sync -> chạy trong thread để không block event loop.
            return await asyncio.to_thread(
                gemini.models.generate_content, model=_MODEL, contents=contents, config=config,
            )
        except _RETRYABLE as e:
            code = getattr(e, "code", None)
            # Chỉ retry lỗi tạm thời (5xx, 429). Lỗi 4xx khác (bad request) -> raise ngay.
            if code is not None and code < 500 and code != 429:
                raise
            last_exc = e
            if attempt < len(_RETRY_DELAYS):
                await asyncio.sleep(_RETRY_DELAYS[attempt])
    raise last_exc


# ───────────────────────── PERSONA / SYSTEM PROMPT ─────────────────────────
# Một core, hai persona: Zalo = sale ngắn gọn; Web = trợ lý đa năng.
# Tách theo channel để Web sau này thêm "Tutora là gì / chính sách" mà không đụng Zalo.
_PERSONA = {
    "zalo": (
        "Em là trợ lý của Tutora trên Zalo, giúp phụ huynh tìm gia sư cho con.\n"
        "GIỌNG ĐIỆU: xưng 'em', gọi phụ huynh là 'anh/chị'. Lễ phép, thân thiện, "
        "tự nhiên như người Việt tư vấn thật. Có 'dạ', 'ạ' đúng mực (đừng lạm dụng). "
        "Trả lời NGẮN GỌN (1-2 câu), tiếng Việt có dấu. Tránh dịch máy/cứng nhắc.\n"
        "HIỂU Ý NGƯỜI DÙNG THEO NGỮ CẢNH (quan trọng nhất):\n"
        "- Đọc cả lịch sử hội thoại để hiểu câu ngắn/chung chung. Vd phụ huynh trả lời 'ờ', "
        "'được', 'ok', 'cũng được' NGAY SAU khi em vừa hỏi (vd 'xem chi tiết không ạ?') = "
        "ĐỒNG Ý với câu em hỏi — làm theo, KHÔNG đi tìm gia sư mới.\n"
        "- Chào hỏi/lạc đề ('alo', 'shop ơi', 'chào em'): chào lại thân thiện, hỏi anh/chị "
        "cần tìm gia sư môn gì cho bé. KHÔNG trả 'em chưa có thông tin' cho câu chào.\n"
        "TƯ VẤN TÌM GIA SƯ — LINH HOẠT, ĐỪNG MÁY MÓC:\n"
        "- NGƯỠNG ĐỦ ĐỂ GỢI Ý = có MÔN + LỚP + MỤC TIÊU (mất gốc/củng cố/nâng cao/ôn thi). "
        "Khi đã đủ 3 thứ này, em PHẢI GỌI TOOL search_tutors (KHÔNG chỉ nói 'em tìm...' rồi "
        "hỏi tiếp — đó là sai). Gọi tool xong, dựa kết quả để gợi ý. TUYỆT ĐỐI ĐỪNG hỏi thêm "
        "thầy/cô hay giá TRƯỚC khi search — UX tệ; hỏi tinh chỉnh SAU khi đã gợi ý.\n"
        "  VÍ DỤ: phụ huynh 'tìm gia sư Toán lớp 8 ôn thi' -> ĐỦ -> GỌI search_tutors ngay, "
        "KHÔNG hỏi lại. Phụ huynh 'con lớp 10 mất gốc Lý' -> ĐỦ -> GỌI search_tutors ngay.\n"
        "- Nếu CÒN THIẾU (mới có môn, hoặc môn+lớp nhưng CHƯA rõ mục tiêu): TUYỆT ĐỐI CHƯA "
        "gọi search_tutors — thiếu LỚP thì kết quả trả về sai cấp học, gợi ý sẽ vô nghĩa. "
        "Hỏi THÊM tối đa 1-2 câu then chốt để đủ ngưỡng trên, rồi mới search. Hỏi tự nhiên, "
        "từng câu, không dồn.\n"
        "- CHỈ HỎI CÁI CÒN THIẾU, đừng lặp lại câu hỏi cũ. Đọc kỹ hội thoại: nếu phụ huynh đã "
        "cho MỤC TIÊU ('luyện thi') nhưng thiếu LỚP -> hỏi ĐÚNG 'bé học lớp mấy ạ?', KHÔNG hỏi "
        "lại cả môn+lớp+mục tiêu. Tích luỹ thông tin qua các lượt, không bắt phụ huynh nhắc lại.\n"
        "- Khi gợi ý -> giới thiệu ĐÚNG những gia sư trong field 'shown' của kết quả search "
        "(tối đa 2, đúng số card hiển thị). KHÔNG nói tổng số kiểu 'tìm được 10 gia sư' — phụ "
        "huynh chỉ thấy 2 thẻ nên nói thế gây rối. Chỉ nói về người trong 'shown', mỗi người 1 "
        "dòng ngắn (tên + vì sao hợp).\n"
        "- HỎI CHI TIẾT 1 GIA SƯ: khi phụ huynh hỏi sâu về MỘT gia sư (vd 'chi tiết thầy Đạt'), "
        "CHỈ trả lời về gia sư ĐÓ. TUYỆT ĐỐI KHÔNG tự giới thiệu thêm gia sư khác, KHÔNG gợi ý "
        "người mới (NestJS sẽ gửi lại card thừa). Chỉ thêm người khác khi phụ huynh YÊU CẦU.\n"
        "ĐỊNH DẠNG TIN NHẮN (Zalo — RẤT QUAN TRỌNG):\n"
        "- Zalo KHÔNG render markdown. TUYỆT ĐỐI KHÔNG dùng '**', '*', '#', '`', '-' đầu dòng, "
        "không in đậm/nghiêng. Chỉ viết chữ thuần, xuống dòng bình thường.\n"
        "- TUYỆT ĐỐI KHÔNG để lộ id kỹ thuật (vd 'seed-tutor-101', 'id=...') trong câu trả lời. "
        "Chỉ gọi gia sư bằng TÊN. Phụ huynh không bao giờ thấy id.\n"
        "- Thông tin chi tiết gia sư (ảnh, giá, đánh giá, nút đặt lịch) đã được hiển thị bằng "
        "THẺ riêng bên dưới tin nhắn. Vì vậy trong câu chữ, em CHỈ giới thiệu ngắn gọn vì sao "
        "hợp — KHÔNG liệt kê lại giá/đánh giá/số liệu (tránh trùng với thẻ).\n"
        "ĐỔI MÔN / ĐỔI LỚP (chống gợi ý nhầm — RẤT QUAN TRỌNG):\n"
        "- Khi phụ huynh đổi/thêm MÔN khác (vd đang Toán, hỏi 'gia sư Sinh'): sau khi xác nhận, "
        "GỌI search_tutors và ĐIỀN param 'subject' = tên môn mới (vd 'Sinh học'). Đổi LỚP thì "
        "điền 'grade_level' = số lớp (vd lớp 8 -> 8). PHẢI search lại — gia sư mỗi môn/lớp KHÁC nhau.\n"
        "- TUYỆT ĐỐI KHÔNG lấy gia sư đã gợi ý ở môn trước gán sang môn mới. Mỗi gia sư chỉ dạy "
        "đúng môn của họ. Chỉ giới thiệu gia sư CÓ trong kết quả search_tutors mới nhất. Nếu kết "
        "quả rỗng thì nói chưa tìm được, KHÔNG bịa.\n"
        "- Nếu search_tutors trả về field 'error': ĐỌC nội dung error và làm đúng theo đó "
        "(vd 'chưa biết lớp' -> hỏi phụ huynh bé học lớp mấy; 'môn không có trong hệ thống' -> "
        "nói thật Tutora chưa hỗ trợ môn đó). TUYỆT ĐỐI KHÔNG giới thiệu gia sư nào khi có error "
        "(kể cả gia sư đã gợi ý trước đó), KHÔNG bịa thêm thông tin.\n"
        "DÙNG TOOL:\n"
        "- search_tutors: khi đã đủ ngữ cảnh để tìm/đổi tiêu chí gia sư. Điền 'subject'/'grade_level_id' khi đổi môn/lớp. "
        "Nếu phụ huynh nêu nhu cầu ĐẶC THÙ (mất gốc, ôn thi, cần gia sư kiên nhẫn/nghiêm khắc, phương pháp dạy...) "
        "-> điền tóm tắt vào param 'query' để hệ thống tìm gia sư khớp nhất; chỉ nêu môn/lớp/giá thì bỏ trống.\n"
        "- get_tutor_detail: khi hỏi sâu về MỘT gia sư trong danh sách đã gợi ý "
        "(bằng cấp, kinh nghiệm, phong cách dạy).\n"
        "- get_tutor_availability: khi hỏi gia sư rảnh giờ nào / lịch trống.\n"
        "- answer_faq: khi hỏi về Tutora (cách hoạt động, chính sách, giá chung).\n"
        "- confirm_action: GỌI TRƯỚC khi đổi ngữ cảnh (đổi môn/lớp/bé/mục tiêu) hoặc khi "
        "phụ huynh muốn đặt lịch. KHÔNG tự đổi tiêu chí, KHÔNG tự đặt lịch — luôn confirm trước.\n"
        "CHỐNG BỊA — RẤT QUAN TRỌNG: thông tin về Tutora (cách hoạt động, chính sách, giá, "
        "hoàn tiền...) CHỈ được lấy từ kết quả tool answer_faq. Nếu answer_faq trả về RỖNG "
        "(không có passages), em PHẢI nói thật: 'Dạ phần này em chưa có thông tin, anh/chị "
        "liên hệ hỗ trợ Tutora để được giải đáp chính xác giúp em nhé ạ' — TUYỆT ĐỐI KHÔNG "
        "tự bịa câu trả lời từ kiến thức chung. Tương tự, không bịa thông tin gia sư, giá, lịch.\n"
        "- Passages CÓ nhưng KHÔNG trả lời đúng câu hỏi (vd hỏi hoàn tiền mà passage chỉ nói "
        "Tutora là gì): CHỈ trả lời phần passage thật sự cover; phần còn lại PHẢI nói rõ "
        "'phần này em chưa có thông tin, anh/chị liên hệ hỗ trợ Tutora giúp em ạ'. KHÔNG lái "
        "sang trả lời chuyện khác để né câu hỏi."
    ),
    "web": (
        "Em là trợ lý của Tutora trên web. Xưng 'em', gọi người dùng là 'anh/chị', "
        "lễ phép và tự nhiên như người Việt. Giúp phụ huynh tìm gia sư VÀ trả lời câu hỏi "
        "chung về Tutora (Tutora là gì, cách hoạt động, chính sách). Tiếng Việt có dấu, rõ ràng. "
        "Dùng tool search_tutors / get_tutor_detail / get_tutor_availability / answer_faq, "
        "không bịa. KHÔNG tự xử lý đặt lịch/thanh toán."
    ),
}


# Zalo không render markdown và không nên lộ id kỹ thuật. Persona đã dặn LLM,
# nhưng strip thêm 1 lớp ở đây để chắc chắn (LLM đôi khi vẫn lỡ chèn).
# Bắt mọi dạng id rò rỉ: "(id=seed-tutor-11)", "(ID: seed-tutor-11)", "id = abc-1",
# và cả dạng KHÔNG có dấu tách: "ID seed-tutor-999" (chỉ có khoảng trắng).
# Separator [:=]? optional -> gộp chung 2 case "có dấu" và "chỉ có space" vào 1 regex.
# Ưu tiên xoá cả ngoặc trước; sau đó xoá dạng trần còn sót, giữ lại 1 space.
_ID_LEAK_PAREN_RE = re.compile(r"\s*[\(\[]\s*id\s*[:=]?\s*[\w-]+\s*[\)\]]", re.IGNORECASE)
_ID_LEAK_BARE_RE = re.compile(r"\bid\s*[:=]?\s*[\w-]{3,}\s*", re.IGNORECASE)
# Lưới cuối: xoá token có HÌNH DẠNG tutor id bất kể văn cảnh (model có thể viết
# 'id là "seed-tutor-999"' — chen từ/ngoặc giữa "id" và token khiến 2 regex trên hụt).
# Bắt: seed-* (dev data) và UUID (production id). Kèm ngoặc kép/nháy bao quanh nếu có.
_ID_TOKEN_RE = re.compile(
    r"[\"'“”]?\b(?:seed-[\w-]+|[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})\b[\"'“”]?",
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
    # Gọn khoảng trắng thừa do strip để lại.
    text = re.sub(r"[ \t]{2,}", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


# ───────────────────────── KHAI BÁO TOOL (schema cho LLM) ─────────────────────────
# Tool narrow, job-specific, schema rõ — LLM chỉ điền tham số, không biết internals.
_TOOL_DECLS = [
    types.FunctionDeclaration(
        name="search_tutors",
        description=(
            "Tìm danh sách gia sư phù hợp theo tiêu chí phụ huynh nêu. "
            "Gọi khi phụ huynh muốn tìm/xem gia sư hoặc đổi tiêu chí (giá, môn, giới tính)."
        ),
        parameters=types.Schema(
            type=types.Type.OBJECT,
            properties={
                # query: nhu cầu đặc thù ngoài filter cứng -> kích hoạt semantic rerank
                # (embedding + pgvector) ở Ranking Core. Không điền = chỉ sort theo chất lượng.
                "query": types.Schema(type=types.Type.STRING, description="Mô tả nhu cầu ĐẶC THÙ của phụ huynh bằng 1 câu ngắn tiếng Việt, NẾU có: mục tiêu học (mất gốc/ôn thi/nâng cao), tính cách gia sư mong muốn (kiên nhẫn, nghiêm khắc...), phương pháp dạy. Vd: 'con mất gốc Toán cần gia sư kiên nhẫn dạy từ căn bản'. KHÔNG điền nếu phụ huynh chỉ nêu môn/lớp/giá."),
                # subject: CHỈ điền khi phụ huynh ĐỔI/THÊM môn so với môn đang tư vấn.
                # Không điền -> giữ môn hiện hành (context). Backend map tên -> subjectId.
                "subject": types.Schema(type=types.Type.STRING, description="Tên môn học NẾU phụ huynh đổi/thêm môn (vd 'Toán', 'Ngữ văn', 'Sinh học', 'Hóa học'). KHÔNG điền nếu vẫn môn cũ."),
                "grade_level": types.Schema(type=types.Type.INTEGER, description="Số lớp (1-12) NẾU phụ huynh đổi lớp (vd 'lớp 8' -> 8). KHÔNG điền nếu vẫn lớp cũ."),
                "min_rate": types.Schema(type=types.Type.NUMBER, description="Giá tối thiểu VND/giờ nếu nêu (vd 'trên 150k' -> 150000)"),
                "max_rate": types.Schema(type=types.Type.NUMBER, description="Giá tối đa VND/giờ nếu nêu (vd 'dưới 200k' -> 200000)"),
                "tutor_gender": types.Schema(type=types.Type.STRING, enum=["male", "female"], description="Giới tính gia sư nếu nêu"),
                "desired_count": types.Schema(type=types.Type.INTEGER, description="Số gia sư muốn xem nếu nêu (vd '1-2 người' -> 2)"),
            },
        ),
    ),
    types.FunctionDeclaration(
        name="answer_faq",
        description=(
            "Tra cứu thông tin chung về Tutora (cách hoạt động, chính sách, giá chung, "
            "Tutora là gì). Gọi khi phụ huynh hỏi câu KHÔNG phải về một gia sư cụ thể."
        ),
        parameters=types.Schema(
            type=types.Type.OBJECT,
            properties={
                "question": types.Schema(type=types.Type.STRING, description="Câu hỏi của phụ huynh, nguyên văn"),
            },
            required=["question"],
        ),
    ),
    types.FunctionDeclaration(
        name="get_tutor_detail",
        description=(
            "Lấy thông tin chi tiết của MỘT gia sư cụ thể (mô tả, kinh nghiệm, học vấn, "
            "đánh giá). Gọi khi phụ huynh hỏi sâu về một gia sư trong danh sách đã gợi ý "
            "(vd 'cho xem kỹ gia sư A', 'gia sư thứ 2 dạy sao')."
        ),
        parameters=types.Schema(
            type=types.Type.OBJECT,
            properties={
                "tutor_id": types.Schema(type=types.Type.STRING, description="ID gia sư — lấy từ danh sách đã gợi ý trong ngữ cảnh"),
            },
            required=["tutor_id"],
        ),
    ),
    types.FunctionDeclaration(
        name="get_tutor_availability",
        description=(
            "Lấy lịch rảnh / thời khoá biểu của một gia sư cụ thể. Gọi khi phụ huynh "
            "hỏi gia sư rảnh khi nào, dạy được giờ nào, lịch trống ra sao."
        ),
        parameters=types.Schema(
            type=types.Type.OBJECT,
            properties={
                "tutor_id": types.Schema(type=types.Type.STRING, description="ID gia sư — lấy từ danh sách đã gợi ý trong ngữ cảnh"),
            },
            required=["tutor_id"],
        ),
    ),
    types.FunctionDeclaration(
        name="confirm_action",
        description=(
            "GỌI tool này TRƯỚC khi thực hiện hành động NHẠY CẢM, để hỏi xác nhận phụ huynh:\n"
            "- ĐỔI NGỮ CẢNH: phụ huynh muốn đổi môn / đổi lớp-cấp học / đổi sang bé khác / "
            "đổi mục tiêu học (việc này sẽ tìm lại gia sư từ đầu).\n"
            "- BOOKING: phụ huynh muốn đặt lịch / đăng ký học với một gia sư cụ thể.\n"
            "KHÔNG tự đổi tiêu chí hay đặt lịch — luôn confirm trước. Sau khi gọi tool này, "
            "DỪNG, chờ phụ huynh trả lời ở lượt sau."
        ),
        parameters=types.Schema(
            type=types.Type.OBJECT,
            properties={
                "type": types.Schema(type=types.Type.STRING, enum=["context_change", "booking"], description="Loại hành động nhạy cảm"),
                "question": types.Schema(type=types.Type.STRING, description="Câu hỏi xác nhận ngắn, giọng 'em' gọi 'anh/chị' (vd 'Dạ anh/chị muốn đổi sang môn Toán cho bé, đúng không ạ?')"),
                "options": types.Schema(type=types.Type.ARRAY, items=types.Schema(type=types.Type.STRING), description="2-3 lựa chọn ngắn cho phụ huynh bấm (vd ['Đúng rồi', 'Không, giữ như cũ'])"),
            },
            required=["type", "question"],
        ),
    ),
    # ───────── GĐ2 — BOOKING (chưa bật) ─────────
    # Khi scale lên đặt lịch + QR trên Zalo, THÊM tool vào đây. Lưu ý phân vai:
    #   create_booking_draft(tutor_id, date, time, sessions)
    #         -> .NET tạo NHÁP + tự tính tiền (KHÔNG để LLM tính). Trả về tóm tắt + số tiền.
    #   confirm_and_generate_qr(draft_id) -> CHỈ sau khi phụ huynh xác nhận deterministic;
    #         payment service sinh QR, KHÔNG phải LLM. NestJS render ảnh QR gửi Zalo.
    # Agent vẫn chỉ THU THẬP slot bằng hội thoại; chốt tiền là bước xác nhận có nút bấm.
]


# ───────────────────────── HELPER GỌI .NET ─────────────────────────
async def _dotnet_get(path: str) -> dict | None:
    """GET .NET, trả content đã bóc {content: ...}. None nếu lỗi/không tìm thấy."""
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


# ───────────────────────── THỰC THI TOOL (deterministic) ─────────────────────────
async def _resolve_subject_id(subject_name: str | None) -> int | None:
    """Map tên môn (LLM điền, vd 'Sinh học') -> subjectId qua /api/subjects. None nếu không khớp."""
    if not subject_name:
        return None
    subjects = await _get_subjects()
    norm = subject_name.strip().lower()
    # Khớp chính xác trước, rồi khớp chứa (xử lý 'Sinh' vs 'Sinh Học', 'Văn' vs 'Ngữ văn').
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
    """Lấy [{gradeLevelId, gradeName, levelOrder}] từ .NET; cache trong process.
    gradeLevelId KHÔNG tuần tự theo lớp (vd Lớp 8 = id 56) -> phải map qua levelOrder."""
    global _grade_levels_cache
    if _grade_levels_cache:
        return _grade_levels_cache
    try:
        url = f"{_settings.dotnet_be_url}/api/grade-levels"
        async with httpx.AsyncClient(timeout=15) as client:
            r = await client.get(url, headers={"Accept": "application/json"})
            r.raise_for_status()
            data = r.json()
        _grade_levels_cache = data.get("content", data) or []
    except Exception as e:
        print(f"agent grade-levels fetch error: {e}")
    return _grade_levels_cache


async def _resolve_grade_id(grade: int | None) -> int | None:
    """Map số lớp (LLM điền, vd 8) -> gradeLevelId thật (vd 56) qua levelOrder. None nếu không khớp."""
    if not grade:
        return None
    levels = await _get_grade_levels()
    for g in levels:
        if g.get("levelOrder") == grade:
            return g.get("gradeLevelId")
    return None


async def _search_tutors(args: dict, ctx, patch_out: dict) -> dict:
    """Bọc _fetch_candidates (.NET /recommend). Trả tóm tắt cho LLM + full list để render.
    Nếu LLM điền 'subject' (đổi/thêm môn) -> map sang id, cập nhật ctx + patch_out để
    NestJS lưu (tránh kẹt môn cũ). 'grade_level_id' tương tự cho đổi lớp."""
    # Đổi MÔN: LLM điền tên môn -> map id -> ghi đè ctx.subject_id (recommend filter đúng).
    requested_subject = args.get("subject")
    if requested_subject:
        new_subject_id = await _resolve_subject_id(requested_subject)
        if new_subject_id is None:
            # Môn LLM điền không khớp môn nào trong hệ thống -> báo lỗi tường minh qua
            # function_response thay vì âm thầm giữ ctx.subject_id cũ (tránh model bịa
            # gia sư sai môn). Model tự đọc lỗi này và báo thật cho phụ huynh.
            return {
                "_full": [], "shown_count": 0, "shown": [],
                "error": f"môn '{requested_subject}' không có trong hệ thống Tutora",
            }
        ctx.subject_id = new_subject_id
        patch_out["subject_id"] = new_subject_id
    # Đổi LỚP: LLM điền số lớp (1..12); map sang gradeLevelId thật (không tuần tự, vd Lớp 8=56).
    requested_grade = args.get("grade_level")
    if requested_grade:
        new_grade_id = await _resolve_grade_id(requested_grade)
        if new_grade_id is None:
            return {
                "_full": [], "shown_count": 0, "shown": [],
                "error": f"lớp {requested_grade} không hợp lệ (chỉ hỗ trợ lớp 1-12)",
            }
        ctx.grade_level_id = new_grade_id
        patch_out["grade_level_id"] = new_grade_id

    # Gate deterministic: LỚP là filter cứng — search không có lớp trả gia sư sai cấp
    # học, gợi ý vô nghĩa. flash-lite hay search sớm dù persona đã cấm -> chặn ở code
    # (persona chỉ là hope-based), trả lỗi hướng dẫn để model hỏi lại phụ huynh.
    if ctx.grade_level_id is None:
        return {
            "_full": [], "shown_count": 0, "shown": [],
            "error": "chưa biết bé học lớp mấy — hỏi phụ huynh LỚP của bé trước rồi mới tìm được gia sư đúng cấp học",
        }

    filters = TutorChatFilters(
        min_rate=args.get("min_rate"),
        max_rate=args.get("max_rate"),
        tutor_gender=args.get("tutor_gender"),
        desired_count=args.get("desired_count"),
        subject_id=ctx.subject_id,  # truyền tường minh để recommend lọc đúng môn hiện hành
    )
    try:
        content = await _fetch_candidates(ctx, filters, query=args.get("query") or "")
        tutors = content.get("tutors", []) or []
        # Thứ tự do Ranking Core quyết (Bayesian + blend) — KHÔNG re-sort đè lên.
        # Chỉ khi core fail (aiRanked=false, .NET fallback SQL order) mới hạ gia sư
        # 0-review xuống cuối làm lưới an toàn.
        if not content.get("aiRanked"):
            tutors.sort(key=lambda t: (t.get("totalReviews") or 0) == 0)
    except Exception as e:
        print(f"agent search_tutors error: {e}")
        return {"_full": [], "count": 0, "error": "không tải được danh sách gia sư"}
    # _full: list đầy đủ trả về client render card (KHÔNG đưa cho LLM, tránh tốn token).
    # NestJS chỉ render _MAX_CARDS_SHOWN card đầu -> LLM chỉ được giới thiệu đúng bấy nhiêu người,
    # nói đúng số đó (tránh "tìm được 10" trong khi chỉ hiện 2 -> phụ huynh confuse).
    shown = tutors[:_MAX_CARDS_SHOWN]
    summary = [
        {"name": t.get("fullName") or t.get("name"), "rating": t.get("averageRating"),
         "rate": t.get("hourlyRate") or t.get("priceMin")}
        for t in shown
    ]
    return {"_full": tutors, "shown_count": len(shown), "shown": summary}


async def _answer_faq(args: dict, gemini: genai.Client) -> dict:
    """Bọc retrieve_chunks trên KB Tutora. Trả các đoạn văn để LLM diễn đạt lại."""
    try:
        chunks, _ = await retrieve_chunks(
            get_supabase(), None, args["question"],
            gemini=gemini, subject="tutora_kb",   # KB riêng cho thông tin Tutora
            # 0.6: chỉ lọc câu LẠC ĐỀ (đo thực tế: lạc đề ~0.45, cùng chủ đề ≥0.64).
            # Passage có trả lời được câu hỏi không -> LLM quyết theo persona chống bịa.
            min_similarity=0.6,
        )
    except Exception as e:
        print(f"agent answer_faq error: {e}")
        return {"passages": []}
    return {"passages": [c.get("content") or c.get("text") for c in chunks]}


async def _get_tutor_detail(args: dict, allowed_ids: set[str]) -> dict:
    """Chi tiết 1 gia sư (.NET /full-profile). Chỉ cho phép id trong list đã gợi ý."""
    tid = args.get("tutor_id")
    if tid not in allowed_ids:
        # Chặn LLM bịa id ngoài danh sách đã gợi ý.
        return {"error": "gia sư này không có trong danh sách đã gợi ý"}
    detail = await _dotnet_get(f"/api/tutors/{tid}/full-profile")
    return detail or {"error": "không lấy được thông tin gia sư"}


async def _get_tutor_availability(args: dict, allowed_ids: set[str]) -> dict:
    """Lịch rảnh 1 gia sư (.NET /schedule). Chỉ cho phép id trong list đã gợi ý."""
    tid = args.get("tutor_id")
    if tid not in allowed_ids:
        return {"error": "gia sư này không có trong danh sách đã gợi ý"}
    sched = await _dotnet_get(f"/api/tutors/{tid}/schedule")
    return sched or {"error": "không lấy được lịch gia sư"}


async def _exec_tool(name: str, args: dict, ctx, gemini: genai.Client, allowed_ids: set[str], patch_out: dict) -> dict:
    """Map function_call của LLM -> code thật. Trả dict (sẽ thành function_response)."""
    if name == "search_tutors":
        return await _search_tutors(args, ctx, patch_out)
    if name == "answer_faq":
        return await _answer_faq(args, gemini)
    if name == "get_tutor_detail":
        return await _get_tutor_detail(args, allowed_ids)
    if name == "get_tutor_availability":
        return await _get_tutor_availability(args, allowed_ids)
    return {"error": f"unknown tool {name}"}


# AGENT LOOP
async def run_agent(body: AgentRequest) -> AgentResponse:
    """Vòng lặp function-calling: LLM quyết định -> ta thực thi tool -> trả kết quả -> lặp."""
    gemini: genai.Client = get_gemini_client()
    persona = _PERSONA.get(body.channel, _PERSONA["zalo"])

    # List gia sư đã gợi ý turn trước -> để agent hiểu "gia sư A" là ai, và CHẶN bịa id.
    allowed_ids: set[str] = {t.tutor_id for t in body.shown_tutors}
    if body.shown_tutors:
        shown = "; ".join(f"{t.name or '?'} (id={t.tutor_id})" for t in body.shown_tutors)
        persona = persona + (
            f"\n\nGia sư đã gợi ý cho phụ huynh (dùng đúng id khi gọi tool chi tiết/lịch): {shown}"
        )

    # Dựng history dạng Content (role user/model).
    contents: list[types.Content] = [
        types.Content(role=("user" if m.role == "user" else "model"),
                      parts=[types.Part.from_text(text=m.content)])
        for m in body.history
    ]
    contents.append(types.Content(role="user", parts=[types.Part.from_text(text=body.message)]))

    # KHÔNG ép tool (AUTO): model TỰ HIỂU ý định + ngữ cảnh hội thoại rồi quyết định
    # gọi tool hay trả lời/hỏi thêm. Câu chung chung ('ờ','được') không bị ép search;
    # câu đủ ý ('tìm gia sư Toán lớp 8 ôn thi') thì tự gọi search. Linh hoạt, NLP thật.
    config = types.GenerateContentConfig(
        system_instruction=persona,
        tools=[types.Tool(function_declarations=_TOOL_DECLS)],
        temperature=0.3,
        # automatic_function_calling tắt -> ta tự chạy loop để kiểm soát (gate booking, log).
        automatic_function_calling=types.AutomaticFunctionCallingConfig(disable=True),
    )

    collected_tutors: list = []   # gom kết quả search để trả kèm response (render card)
    forced_search = False          # đã ép search 1 lần chưa (vá tật flash-lite lười tool)
    forced_confirm = False         # đã ép confirm_action 1 lần chưa (vá tật model tự bịa câu hỏi xác nhận)
    # Môn/lớp đổi giữa chat -> tích vào đây, trả về context_patch cho NestJS lưu.
    patch_out: dict = {}

    def _patch() -> "AgentContextPatch | None":
        return AgentContextPatch(**patch_out) if patch_out else None

    # Cấu hình ép GỌI search_tutors (mode ANY, chỉ tool này) — dùng khi model "nói sẽ tìm
    # gia sư" nhưng không gọi tool (flash-lite ở AUTO hay lười). Giữ NLP của AUTO + đảm bảo search.
    force_search_cfg = types.GenerateContentConfig(
        system_instruction=persona,
        tools=[types.Tool(function_declarations=_TOOL_DECLS)],
        tool_config=types.ToolConfig(function_calling_config=types.FunctionCallingConfig(
            mode=types.FunctionCallingConfigMode.ANY, allowed_function_names=["search_tutors"],
        )),
        temperature=0.3,
        automatic_function_calling=types.AutomaticFunctionCallingConfig(disable=True),
    )
    def _should_force_search(model_text: str) -> bool:
        # CHỈ ép search khi model TỰ NÓI SẼ TÌM (vd 'em tìm gia sư Toán...') mà quên gọi tool.
        # KHÔNG ép khi: chào hỏi, đổi môn (phải confirm), đã liệt kê gia sư rồi, hay câu phủ định.
        t = model_text.lower()
        intends = ("em tìm gia sư" in t or "em sẽ tìm" in t or "em tìm cho" in t
                   or "để em tìm" in t or "em tìm giúp" in t)
        # nếu model đã liệt kê gia sư (có '- ' hoặc 'tìm được') thì KHÔNG phải bỏ lỡ tool
        already_listed = "tìm được" in t or "\n- " in model_text or "gồm" in t
        return intends and not already_listed

    # Cấu hình ép GỌI confirm_action — cùng pattern với force_search_cfg. Vá tật model
    # tự bịa câu hỏi xác nhận bằng text thường thay vì gọi tool (mất cờ awaiting_confirmation/
    # handoff_to_booking mà NestJS cần để render nút + chuyển luồng booking deterministic).
    force_confirm_cfg = types.GenerateContentConfig(
        system_instruction=persona,
        tools=[types.Tool(function_declarations=_TOOL_DECLS)],
        tool_config=types.ToolConfig(function_calling_config=types.FunctionCallingConfig(
            mode=types.FunctionCallingConfigMode.ANY, allowed_function_names=["confirm_action"],
        )),
        temperature=0.3,
        automatic_function_calling=types.AutomaticFunctionCallingConfig(disable=True),
    )

    def _should_force_confirm(model_text: str) -> bool:
        # Phát hiện model tự đặt câu hỏi xác nhận bằng text thay vì gọi confirm_action.
        # Dấu hiệu câu hỏi xác nhận: "đúng không/ko", "phải không/ko", hoặc model tự nói
        # đang xác nhận ("xác nhận lại", "xác nhận giúp em", "cho em xác nhận").
        t = model_text.lower()
        is_confirm_question = ("đúng không" in t or "đúng ko" in t or "phải không" in t
                                or "phải ko" in t or "xác nhận" in t)
        if not is_confirm_question:
            return False
        wants_context_change = (("đổi" in t or "chuyển" in t or "thay" in t)
                                 and ("môn" in t or "lớp" in t or "bé" in t))
        wants_booking = "đặt lịch" in t or "chốt" in t or "đăng ký học" in t
        return wants_context_change or wants_booking

    try:
        for _ in range(_MAX_TURNS):
            resp = await _generate_with_retry(gemini, contents, config)

            calls = resp.function_calls or []
            if not calls:
                text = _sanitize_reply((resp.text or "").strip())
                # Model tự bịa câu hỏi xác nhận bằng text thay vì gọi confirm_action -> ép gọi
                # lại tool (mode=ANY, chỉ confirm_action) để lấy đúng type/question có cấu trúc,
                # thay vì trả text rời rạc thiếu awaiting_confirmation/handoff_to_booking.
                if (not forced_confirm and _should_force_confirm(text)):
                    forced_confirm = True
                    contents.append(resp.candidates[0].content)
                    resp = await _generate_with_retry(gemini, contents, force_confirm_cfg)
                    calls = resp.function_calls or []
                # Model nói sẽ tìm gia sư mà KHÔNG gọi tool + chưa search lần nào -> ép search.
                elif (not forced_search and not collected_tutors and _should_force_search(text)):
                    forced_search = True
                    contents.append(resp.candidates[0].content)
                    resp = await _generate_with_retry(gemini, contents, force_search_cfg)
                    calls = resp.function_calls or []
                if not calls:
                    # Vòng lặp đã có tool result trước đó (collected_tutors) nhưng model không
                    # sinh text tóm tắt -> ép thêm 1 lượt yêu cầu tóm tắt, tránh trả reply rỗng
                    # cho phụ huynh (card gia sư hiện ra không lời giới thiệu).
                    if not text and collected_tutors:
                        contents.append(resp.candidates[0].content)
                        follow_up = await _generate_with_retry(gemini, contents, config)
                        follow_text = _sanitize_reply((follow_up.text or "").strip())
                        if follow_text and not (follow_up.function_calls or []):
                            text = follow_text
                        elif not text:
                            # Fallback template: model vẫn không sinh text -> tổng hợp từ
                            # danh sách gia sư đã có, không để phụ huynh nhận màn hình trống.
                            names = [t.get("fullName") or t.get("name") for t in collected_tutors[:_MAX_CARDS_SHOWN] if t]
                            text = ("Dạ em tìm được gia sư " + " và ".join(n for n in names if n)
                                    + " phù hợp ạ. Anh/chị xem chi tiết giúp em nhé!") if names else (
                                "Dạ em tìm được vài gia sư phù hợp, anh/chị xem chi tiết giúp em nhé!"
                            )
                    return AgentResponse(reply=text, tutors=collected_tutors, context_patch=_patch())

            # confirm_action: điểm nhạy cảm (đổi ngữ cảnh / booking) -> DỪNG, hỏi xác nhận.
            # Không thực hiện hành động; NestJS render nút, chờ phụ huynh bấm lượt sau.
            confirm = next((c for c in calls if c.name == "confirm_action"), None)
            if confirm:
                args = dict(confirm.args or {})
                ctype = args.get("type")
                return AgentResponse(
                    reply=args.get("question") or "Dạ anh/chị xác nhận giúp em nhé ạ?",
                    tutors=collected_tutors,
                    awaiting_confirmation=True,
                    confirm_type=ctype,
                    handoff_to_booking=(ctype == "booking"),
                    suggestions=list(args.get("options") or []),
                    context_patch=_patch(),
                )

            # Có tool call: append turn model + thực thi từng tool + append function_response.
            contents.append(resp.candidates[0].content)
            tool_parts = []
            for call in calls:
                result = await _exec_tool(call.name, dict(call.args or {}), body.context, gemini, allowed_ids, patch_out)
                # answer_faq rỗng -> trả thẳng câu fallback, KHÔNG phó mặc model tự xoay
                # (flash-lite hay im lặng/bịa khi passages rỗng). Chống bịa chắc chắn.
                if call.name == "answer_faq" and not (result.get("passages") or []):
                    return AgentResponse(
                        reply="Dạ phần này em chưa có thông tin ạ. Anh/chị liên hệ hỗ trợ Tutora để được giải đáp chính xác giúp em nhé!",
                        tutors=collected_tutors,
                    )
                # _full = list gia sư đầy đủ để render card; KHÔNG gửi lại cho LLM (tốn token).
                if call.name == "search_tutors":
                    collected_tutors = result.pop("_full", []) or collected_tutors
                    # Cho phép hỏi chi tiết gia sư vừa search ngay trong cùng phiên.
                    allowed_ids |= {t.get("tutorId") or t.get("tutor_id") for t in collected_tutors if t}
                tool_parts.append(types.Part.from_function_response(name=call.name, response=result))
            contents.append(types.Content(role="user", parts=tool_parts))
    except _RETRYABLE as e:
        # Gemini lỗi tạm thời (503/429/timeout) cả sau retry -> fallback graceful,
        # KHÔNG ném 500 cho NestJS (tránh bot "chết câm" với phụ huynh).
        print(f"agent gemini error sau retry: {e}")
        return AgentResponse(
            reply="Dạ em xin lỗi, hệ thống đang hơi bận. Anh/chị nhắn lại giúp em sau giây lát nhé!",
            tutors=collected_tutors,
        )

    # Hết MAX_TURNS mà vẫn gọi tool -> trả lời an toàn thay vì lặp mãi.
    return AgentResponse(
        reply="Dạ em cần thêm chút thông tin ạ. Anh/chị mô tả lại nhu cầu giúp em nhé?",
        tutors=collected_tutors,
        handoff_to_booking=False,
        context_patch=_patch(),
    )
