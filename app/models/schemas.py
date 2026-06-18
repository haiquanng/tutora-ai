from pydantic import BaseModel
from typing import Optional, List

class HistoryMessage(BaseModel):
    role: str   # "user" | "assistant"
    content: str

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
