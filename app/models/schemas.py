from pydantic import BaseModel, field_validator
from typing import Optional, List, Any, Dict, Literal

class HistoryMessage(BaseModel):
    role: str   # "user" | "assistant"
    content: str

class TutorChatContext(BaseModel):
    subject_id: Optional[int] = None
    grade_level_id: Optional[int] = None
    teaching_mode: Optional[str] = None
    city: Optional[str] = None
    # Slot hội thoại (agent slot-filling) — NestJS persist qua context_patch giống grade.
    # goal: mục tiêu học (mất gốc/củng cố/nâng cao/ôn thi/luyện SAT...). preferences: mong
    # muốn về gia sư (tính cách, cách dạy, hình thức). asked_preferences: đã hỏi lượt gộp
    # tuỳ chọn (khu vực/hình thức + mong muốn) chưa — gate "hỏi 1 lần, mềm" của KB-A bước 4:
    # hỏi đúng 1 lần rồi search dù PH có trả lời hay không (agents/agentscenarios.md).
    goal: Optional[str] = None
    preferences: Optional[str] = None
    asked_preferences: Optional[bool] = None
    # Ngân sách PH nêu (vd "dưới 200k" -> max_rate=200000) — filter CỨNG thật truyền sang
    # .NET /recommend (minRate/maxRate), không chỉ nằm mờ trong text query preferences.
    min_rate: Optional[float] = None
    max_rate: Optional[float] = None
    # "male" | "female" | null — PH ưu tiên giới tính gia sư (không bắt buộc).
    tutor_gender: Optional[str] = None
    # "vi" | "en" — bot (tutora-zalo-bot) tự nhận diện từ tin nhắn PH, gửi kèm mỗi lượt.
    # Dùng để CHỈ ĐỊNH tường minh ngôn ngữ trả lời cho _say() thay vì chỉ suy luận từ
    # tin nhắn cuối (từng không đủ mạnh khi task nội bộ dài và toàn tiếng Việt).
    preferred_language: Optional[str] = None
    # ── State 2-lượt "muốn tìm gia sư nữa" sau khi đã Matched (agents/agentscenarios.md
    # cập nhật 2026-07-13) — PH đã được giới thiệu gia sư, giờ muốn tìm thêm/tìm lại:
    # bot PHẢI hỏi rõ trước khi quyết định tìm trong chat hay mở lại Mini App form. ──
    # True = lượt trước VỪA hỏi "đổi gia sư khác (giữ tiêu chí)" hay "nhu cầu khác hẳn"
    # — lượt này đọc câu trả lời để rẽ nhánh, KHÔNG chạy pipeline tìm kiếm bình thường.
    pending_reopen_choice: Optional[bool] = None
    # True = PH vừa chọn "đổi gia sư khác, giữ tiêu chí" và bot VỪA hỏi lý do/yêu cầu
    # thêm — lượt kế tiếp là câu trả lời đó, dùng để tìm lại NGAY, loại trừ gia sư đã
    # gợi ý (allowed/shown_tutors), không hỏi thêm gì nữa.
    refining_alternate_search: Optional[bool] = None


# ── Session memory: tóm tắt history cũ khi user quay lại sau gap dài ──
class SummarizeSessionRequest(BaseModel):
    # NestJS gửi history phiên cũ khi phát hiện user quay lại (gap > threshold).
    history: List[HistoryMessage] = []
    shown_tutors: List["ShownTutor"] = []   # gia sư đã gợi ý phiên trước (để không bắn lại)

class SessionMemory(BaseModel):
    # Structured facts (nguồn sự thật, NestJS lưu Postgres) — dùng pre-fill search sau.
    subject: Optional[str] = None       # tên môn (vd "Ngữ văn")
    grade: Optional[int] = None         # số lớp (1-12)
    goal: Optional[str] = None          # mục tiêu: mất gốc / củng cố / nâng cao / ôn thi
    budget_max: Optional[float] = None  # ngân sách trần VND/giờ nếu có
    preferences: Optional[str] = None   # mong muốn khác: tính cách gia sư, hình thức học...
    tutors_shown: List[str] = []        # tên gia sư đã gợi ý phiên trước

class SummarizeSessionResponse(BaseModel):
    # recap: 1 câu tiếng Việt để chào lại tự nhiên. memory: facts để lưu + pre-fill.
    recap: str
    memory: SessionMemory
    # has_pending_search: phiên cũ có đang dở việc tìm gia sư không (để welcome-back
    # hỏi "tiếp tục hay tìm mới"); False nếu chỉ chào hỏi linh tinh, không cần recap.
    has_pending_search: bool = False

class TutorChatFilters(BaseModel):
    min_rate: Optional[float] = None
    max_rate: Optional[float] = None
    tutor_gender: Optional[str] = None  # "male" | "female" | null -- cập nhật sang 0,1,2 sau
    # LLM được phép đổi/thêm môn giữa chat → override subject_id của context.
    subject_id: Optional[int] = None
    # Số gia sư PH muốn xem ("cần 1-2 người" → 2). null = mặc định.
    desired_count: Optional[int] = None

class TutorChatRequest(BaseModel):
    history: List[HistoryMessage] = []
    message: str = ""
    context: TutorChatContext = TutorChatContext()
    # Filter đã tích luỹ qua các turn trước (FE giữ & gửi kèm) — service merge,
    # không reset. Nhờ vậy môn/giá đang chọn được duy trì khi PH không nhắc lại.
    current_filters: Optional[TutorChatFilters] = None

class TutorChatResponse(BaseModel):
    reply: str
    tutors: List[Dict[str, Any]] = []   # proxy nguyên shape từ .NET recommend
    filters: TutorChatFilters = TutorChatFilters()
    ai_ranked: bool = False
    # Khi đổi môn: AI hỏi lại xác nhận ngữ cảnh, KHÔNG tìm gia sư turn đó.
    awaiting_confirmation: bool = False
    suggestions: List[str] = []

class ShownTutor(BaseModel):
    # Gia sư đã gợi ý ở turn trước, để agent hiểu "gia sư A" trong list là ai.
    tutor_id: str
    name: Optional[str] = None

class AgentRequest(BaseModel):
    # Stateless: bên gọi (NestJS/Web) giữ history, gửi kèm mỗi request.
    history: List[HistoryMessage] = []
    message: str = ""
    channel: str = "zalo"   # "zalo" (sale) | "web" (đa năng) -> chọn persona
    context: TutorChatContext = TutorChatContext()
    # List gia sư vừa gợi ý (NestJS giữ) -> agent trả lời được "chi tiết gia sư A".
    shown_tutors: List[ShownTutor] = []

class AgentContextPatch(BaseModel):
    subject_id: Optional[int] = None
    grade_level_id: Optional[int] = None
    # Slot hội thoại mới rút được trong lượt này → NestJS lưu, gửi lại lượt sau.
    goal: Optional[str] = None
    preferences: Optional[str] = None
    # True khi agent VỪA hỏi lượt gộp tuỳ chọn — NestJS persist để lượt sau không hỏi lại.
    asked_preferences: Optional[bool] = None
    min_rate: Optional[float] = None
    max_rate: Optional[float] = None
    teaching_mode: Optional[str] = None
    city: Optional[str] = None
    # State 2-lượt "muốn tìm gia sư nữa" (xem TutorChatContext) — patch cả True (bắt đầu hỏi)
    # lẫn False tường minh (đã đọc xong câu trả lời, xoá cờ) để NestJS merge generic đúng.
    pending_reopen_choice: Optional[bool] = None
    refining_alternate_search: Optional[bool] = None

class AgentResponse(BaseModel):
    reply: str
    tutors: List[Dict[str, Any]] = []        # proxy shape từ .NET recommend; render card riêng
    # Cờ bàn giao sang deterministic booking flow (NestJS xử lý, agent KHÔNG tự đặt lịch).
    handoff_to_booking: bool = False
    # PH muốn ĐỔI tiêu chí tìm kiếm giữa chat (đã Matched) -> NestJS gửi lại nút mở Mini
    # App (điền sẵn giá trị cũ từ context hiện có), KHÔNG hỏi qua chat nữa (mở form vốn đã
    # là hành động "an toàn", không cam kết gì cho tới khi PH bấm Tìm gia sư lần nữa).
    reopen_mini_app: bool = False
    # True = PH muốn NHU CẦU KHÁC HẲN (đổi môn/lớp/giới tính...), Mini App PHẢI để form
    # TRỐNG cho PH điền lại từ đầu — KHÔNG được tự auto-search bằng agentCtx cũ (Mini App
    # tự prefill từ /prefill rồi auto-skip qua kết quả nếu đủ subject+grade, xem
    # MiniAppSearchFormPage.tsx). False (mặc định) = an toàn để Mini App auto-skip nếu có
    # đủ dữ liệu cũ (vd resend_form — nút lỗi, tiêu chí không đổi, hoặc lần đầu thiếu slot
    # thì auto-skip cũng vô hại vì chưa có gì để prefill). Bug thật 2026-07-14: thiếu cờ
    # này khiến chọn "nhu cầu khác" vẫn bị auto-skip hiện lại kết quả CŨ trước khi PH kịp
    # điền gì mới.
    reopen_mini_app_fresh: bool = False
    # Khi agent gặp điểm nhạy cảm (đổi lớp/bé/môn, hoặc ý định booking) -> hỏi xác nhận,
    # CHƯA hành động. NestJS render nút từ suggestions, chờ phụ huynh bấm.
    awaiting_confirmation: bool = False
    confirm_type: Optional[str] = None       # "context_change" | "booking"
    suggestions: List[str] = []              # các lựa chọn ngắn cho phụ huynh bấm
    # Môn/lớp mới sau khi đổi giữa chat -> NestJS lưu để các turn sau gửi đúng subject_id.
    context_patch: Optional[AgentContextPatch] = None

# ── Direct search: Mini App gọi thẳng, KHÔNG qua LLM/hội thoại (agent.run_agent) ──
# Dùng khi tiêu chí đã đủ rõ ràng từ form (subject_id/grade_level_id là id thật, không phải
# text cần LLM trích) — vd hiển thị kết quả ngay trong Mini App + nút "tìm gia sư khác".
# KHÔNG đi qua run_agent() vì gate disambiguation "đổi gia sư khác/nhu cầu khác"
# (agent.py::_handle_find_tutor) sẽ chặn lại hỏi qua chat thay vì trả tutor ngay — sai mục
# đích cho tương tác nút bấm trong Mini App, vốn đã rõ ý (không cần hỏi).
class DirectSearchRequest(BaseModel):
    subject_id: int
    grade_level_id: Optional[int] = None
    goal: Optional[str] = None
    preferences: Optional[str] = None
    min_rate: Optional[float] = None
    max_rate: Optional[float] = None
    teaching_mode: Optional[str] = None
    city: Optional[str] = None
    tutor_gender: Optional[str] = None
    # Loại các tutorId này khỏi kết quả — dùng cho nút "tìm gia sư khác" (không lặp lại
    # gia sư đã hiện trước đó).
    exclude_tutor_ids: List[str] = []
    top_k: int = 5

class DirectSearchResponse(BaseModel):
    tutors: List[Dict[str, Any]] = []


class SolveRequest(BaseModel):
    text: Optional[str] = None
    image_base64: Optional[str] = None
    image_url: Optional[str] = None
    grade: Optional[str] = None
    chapter: Optional[str] = None
    message_id: Optional[str] = None
    chat_id: Optional[str] = None
    history: List[HistoryMessage] = []
    response_format: Optional[Literal["markdown", "steps"]] = "markdown"

    @field_validator("response_format", mode="before")
    @classmethod
    def _default_response_format(cls, v):
        return v or "markdown"

class TutorRecommendRequest(BaseModel):
    query: Optional[str] = None
    candidate_ids: List[str] = []
    top_k: int = 10

class TutorRecommendResult(BaseModel):
    tutor_id: str
    similarity: float
    city: Optional[str] = None
    district: Optional[str] = None
    teaching_mode: Optional[str] = None
    subject_ids: Optional[List[int]] = None
    grades: Optional[List[str]] = None
    price_min: Optional[float] = None
    price_max: Optional[float] = None
    average_rating: Optional[float] = None
    total_reviews: Optional[int] = None
    completed_hours: Optional[int] = None

class TutorRecommendResponse(BaseModel):
    results: List[TutorRecommendResult]
    total: int

class StreamChunk(BaseModel):
    id: str
    session_id: str
    delta: str = ""
    done: bool = False
    thinking: Optional[str] = None
    rag_used: Optional[bool] = None
    # response_format="steps": các bước đã cấu trúc cho canvas (xem step_segmenter).
    steps: Optional[List[Dict[str, Any]]] = None
    steps_final: Optional[List[Dict[str, Any]]] = None

class Step(BaseModel):
    step: int
    title: str
    content: str
    formula: Optional[str] = None

class SolveResponse(BaseModel):
    problem_extracted: str
    grade: Optional[str]
    chapter: Optional[str]
    steps: List[Step]
    final_answer: str
    hint: str
    common_mistakes: List[str]
    verified: Optional[bool] = None
    confidence: float
    rag_used: bool


# Resolve forward-ref "ShownTutor" trong SummarizeSessionRequest (định nghĩa sau nó).
SummarizeSessionRequest.model_rebuild()
