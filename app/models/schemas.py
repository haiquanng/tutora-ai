from pydantic import BaseModel
from typing import Optional, List, Any, Dict

class HistoryMessage(BaseModel):
    role: str   # "user" | "assistant"
    content: str

class TutorChatContext(BaseModel):
    subject_id: Optional[int] = None
    grade_level_id: Optional[int] = None
    teaching_mode: Optional[str] = None
    city: Optional[str] = None

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

class AgentResponse(BaseModel):
    reply: str
    tutors: List[Dict[str, Any]] = []        # proxy shape từ .NET recommend; render card riêng
    # Cờ bàn giao sang deterministic booking flow (NestJS xử lý, agent KHÔNG tự đặt lịch).
    handoff_to_booking: bool = False
    # Khi agent gặp điểm nhạy cảm (đổi lớp/bé/môn, hoặc ý định booking) -> hỏi xác nhận,
    # CHƯA hành động. NestJS render nút từ suggestions, chờ phụ huynh bấm.
    awaiting_confirmation: bool = False
    confirm_type: Optional[str] = None       # "context_change" | "booking"
    suggestions: List[str] = []              # các lựa chọn ngắn cho phụ huynh bấm
    # Môn/lớp mới sau khi đổi giữa chat -> NestJS lưu để các turn sau gửi đúng subject_id.
    context_patch: Optional[AgentContextPatch] = None

class SolveRequest(BaseModel):
    text: Optional[str] = None
    image_base64: Optional[str] = None
    image_url: Optional[str] = None
    grade: Optional[str] = None
    chapter: Optional[str] = None
    message_id: Optional[str] = None
    chat_id: Optional[str] = None
    history: List[HistoryMessage] = []

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
    delta: str
    done: bool

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
