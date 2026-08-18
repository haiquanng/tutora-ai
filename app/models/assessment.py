"""Hợp đồng phân tích bài đánh giá đầu vào.

Field alias theo camelCase vì .NET serialize camelCase — FE lấy nguyên payload từ
BE /analysis-input rồi đẩy sang đây, không phải map lại từng field.
"""
from typing import Any, Optional

from pydantic import BaseModel, ConfigDict, Field


def _camel(s: str) -> str:
    head, *rest = s.split("_")
    return head + "".join(w.capitalize() for w in rest)


class _CamelModel(BaseModel):
    # populate_by_name: nhận được cả camelCase (.NET) lẫn snake_case (script/test).
    model_config = ConfigDict(alias_generator=_camel, populate_by_name=True)


class AnalysisItem(_CamelModel):
    display_order: int = 0
    content: str = ""
    question_format: str = ""
    chapter_name: Optional[str] = None
    chapter_slug: Optional[str] = None
    difficulty: Optional[str] = None
    correct_answer: str = ""
    given_answer: Optional[str] = None
    # Bỏ trống — KHÁC hẳn trả lời sai, prompt tách riêng 2 ca này.
    skipped: bool = False
    is_correct: bool = False
    time_spent_seconds: Optional[int] = None


class ChapterStat(_CamelModel):
    chapter_id: Optional[int] = None
    chapter_name: Optional[str] = None
    chapter_slug: Optional[str] = None
    total: int = 0
    correct: int = 0
    skipped: int = 0


class DifficultyStat(_CamelModel):
    difficulty: Optional[str] = None
    total: int = 0
    correct: int = 0
    skipped: int = 0


class AnalysisInput(_CamelModel):
    """Khớp AttemptAnalysisInputResponse bên .NET."""
    attempt_id: str = ""
    user_id: str = ""
    subject_id: int = 0
    subject_name: Optional[str] = None
    grade_level_id: int = 0
    grade_name: Optional[str] = None
    assessment_title: str = ""

    total_questions: int = 0
    correct_count: int = 0
    earned_points: float = 0
    max_points: float = 0
    score_percent: Optional[float] = None
    duration_seconds: Optional[int] = None
    attempt_count: int = 1

    items: list[AnalysisItem] = Field(default_factory=list)
    chapter_stats: list[ChapterStat] = Field(default_factory=list)
    difficulty_stats: list[DifficultyStat] = Field(default_factory=list)


class AnalysisOutput(BaseModel):
    """Kết quả AI. FE ghi ngược về BE /analysis (strengths/weaknesses/path stringify)."""
    level: Optional[str] = None
    summary: str = ""
    confidence: Optional[str] = None
    strengths: list[dict[str, Any]] = Field(default_factory=list)
    weaknesses: list[dict[str, Any]] = Field(default_factory=list)
    # Nguồn dữ liệu cho mindmap ở trang Lộ trình học tập.
    chapter_mastery: list[dict[str, Any]] = Field(default_factory=list)
    recommended_path: list[dict[str, Any]] = Field(default_factory=list)
    next_action: Optional[str] = None


class ProficiencyContext(_CamelModel):
    """Profile trình độ nạp vào prompt /solve để lời giải khớp thực lực."""
    subject_name: Optional[str] = None
    grade_name: Optional[str] = None
    level: Optional[str] = None
    summary: Optional[str] = None
    # Tên chương hổng — chỉ cần tên, không cần cả khối JSON.
    weak_chapters: list[str] = Field(default_factory=list)
    strong_chapters: list[str] = Field(default_factory=list)
