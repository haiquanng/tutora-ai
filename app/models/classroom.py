"""Schema cho bài tập nhanh trong buổi học (trích tài liệu + sinh đề)."""
from pydantic import BaseModel, Field
from typing import List, Optional


class MaterialExtractResponse(BaseModel):
    """Toàn văn tài liệu, có mốc '[trang N]' để trích dẫn nguồn."""
    full_text: str = ""
    page_count: Optional[int] = None
    error: Optional[str] = None


class MaterialSource(BaseModel):
    """1 tài liệu nguồn BE gửi lên. full_text lấy từ learning_material_contents."""
    material_id: int
    title: str
    full_text: str


class GeneratePracticeRequest(BaseModel):
    prompt: str = Field(..., min_length=1, max_length=1000)
    materials: List[MaterialSource] = Field(..., min_length=1)


class GeneratedOption(BaseModel):
    key: str
    text: str


class GeneratedQuestion(BaseModel):
    # mc = trắc nghiệm | essay = tự luận
    format: str
    content: str
    options: Optional[List[GeneratedOption]] = None
    correct_answer: Optional[str] = None
    explanation: Optional[str] = None
    # Nguồn trích -> BE hiện "Trích từ <tài liệu> — trang N"
    source_material_id: Optional[int] = None
    source_page: Optional[int] = None


class GeneratePracticeResponse(BaseModel):
    title: str = ""
    questions: List[GeneratedQuestion] = []
    error: Optional[str] = None
