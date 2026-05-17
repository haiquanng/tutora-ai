from pydantic import BaseModel
from typing import Optional, List

class SolveRequest(BaseModel):
    text: Optional[str] = None
    image_base64: Optional[str] = None
    image_url: Optional[str] = None
    grade: Optional[str] = None
    chapter: Optional[str] = None
    message_id: Optional[str] = None
    chat_id: Optional[str] = None

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
