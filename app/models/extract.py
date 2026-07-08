"""Schema cho API extract-pdf: staff upload PDF -> AI đọc -> list câu hỏi."""
from pydantic import BaseModel
from typing import List, Optional


class ExtractedQuestion(BaseModel):
    content: str                        
    solution: Optional[str] = None      
    problem_type: Optional[str] = None  
    chapter: Optional[str] = None       
    page: Optional[int] = None          


class ExtractPdfResponse(BaseModel):
    total: int
    questions: List[ExtractedQuestion]
    error: Optional[str] = None
