"""Hợp đồng phân tích bài đánh giá đầu vào.

Field alias theo camelCase vì .NET serialize camelCase — FE lấy nguyên payload từ
BE /analysis-input rồi đẩy sang đây, không phải map lại từng field.
"""
from enum import Enum
from typing import Any, Optional

from pydantic import BaseModel, BeforeValidator, ConfigDict, Field
from typing_extensions import Annotated


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


# Schema Gemini phải trả
# Enum để BẢN THÂN model bị ràng buộc chỉ sinh được các giá trị này (response_schema),
# thay vì prompt "xin" rồi hy vọng. Từng gặp model trả 'weak' thay 'gap'


def _lenient_enum(enum: type[Enum]):
    """Giá trị ngoài enum -> None thay vì lỗi validate.
    """

    def _parse(value: Any) -> Any:
        if value is None or isinstance(value, enum):
            return value
        try:
            return enum(value)
        except (ValueError, TypeError):
            return None

    return BeforeValidator(_parse)


class Level(str, Enum):
    beginner = "beginner"
    developing = "developing"
    proficient = "proficient"
    advanced = "advanced"


class Confidence(str, Enum):
    low = "low"
    medium = "medium"
    high = "high"


class Severity(str, Enum):
    minor = "minor"
    moderate = "moderate"
    critical = "critical"


class Verdict(str, Enum):
    solid = "solid"
    shaky = "shaky"
    gap = "gap"


class ChapterNote(BaseModel):
    chapter: str
    chapter_slug: Optional[str] = Field(default=None, alias="chapterSlug")
    note: str = ""

    model_config = ConfigDict(populate_by_name=True)


class WeaknessNote(ChapterNote):
    severity: Annotated[Optional[Severity], _lenient_enum(Severity)] = None


class ImproveItem(BaseModel):
    title: str
    why: str = ""


def _valid_improve(v: Any) -> Any:
    """Bỏ dạng bài hỏng, giữ cả chương: mất 1 gợi ý còn hơn mất chương khỏi mindmap."""
    if not isinstance(v, list):
        return []
    out = []
    for row in v:
        if isinstance(row, ImproveItem):
            out.append(row)
        elif isinstance(row, dict) and isinstance(row.get("title"), str) and row["title"].strip():
            out.append(row)
    return out


class ChapterMastery(ChapterNote):
    correct: int = 0
    total: int = 0
    verdict: Annotated[Optional[Verdict], _lenient_enum(Verdict)] = None
    summary: str = ""
    improve: Annotated[list[ImproveItem], BeforeValidator(_valid_improve)] = Field(default_factory=list)


class PathStep(ChapterNote):
    order: int = 0
    goal: str = ""
    why: str = ""
    # Lọc phần tử rác thay vì bỏ cả bước: mất 1 gợi ý luyện tập còn hơn mất cả bước lộ trình.
    practice: Annotated[list[str], BeforeValidator(
        lambda v: [x for x in v if isinstance(x, str) and x.strip()] if isinstance(v, list) else []
    )] = Field(default_factory=list)
    estimated_sessions: Optional[int] = Field(default=None, alias="estimatedSessions")


class AnalysisSchema(BaseModel):
    """Hình dạng JSON truyền cho Gemini qua response_schema (camelCase như prompt)."""
    level: Annotated[Optional[Level], _lenient_enum(Level)] = None
    summary: str = ""
    confidence: Annotated[Optional[Confidence], _lenient_enum(Confidence)] = None
    strengths: list[ChapterNote] = Field(default_factory=list)
    weaknesses: list[WeaknessNote] = Field(default_factory=list)
    chapterMastery: list[ChapterMastery] = Field(default_factory=list)
    recommendedPath: list[PathStep] = Field(default_factory=list)
    nextAction: str = ""


class AnalysisOutput(BaseModel):
    """Kết quả AI. FE ghi ngược về BE /analysis (strengths/weaknesses/path stringify).

    Vẫn là list[dict] vì .NET/FE ăn nguyên khối JSON; nhưng các dict này đã đi qua
    validate của AnalysisSchema trong analyzer._coerce, không còn là dữ liệu thô.
    """
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
