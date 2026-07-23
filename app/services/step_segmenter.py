import re
from typing import Iterator, List, Optional, TypedDict


class SolutionStep(TypedDict):
    index: int
    title: str
    explanation: str
    formulas: List[str]


# TUTOR_SYSTEM_V2 quy định mỗi bước mở đầu bằng "**Bước N: tên bước**" và CẤM heading (#).
# Bắt thêm biến thể "Bước N." phòng khi model bỏ dấu ** để không mất bước.
_STEP_HEADING = re.compile(
    r"^\s*(?:\*\*)?\s*Bước\s*(\d+)\s*[:.)-]?\s*(.*?)(?:\*\*)?\s*$",
    re.IGNORECASE,
)

# Công thức đứng riêng $$...$$ -> tách ra để canvas render nổi bật.
_DISPLAY_FORMULA = re.compile(r"\$\$(.+?)\$\$", re.DOTALL)

# Dòng chốt đáp án / mẹo nhớ (blockquote) — gom thành bước "Kết luận" thay vì bỏ rơi.
_RESULT_LINE = re.compile(r"^\s*(?:>|\*\*(?:Kết quả|Đáp án))", re.IGNORECASE)


def _split_formulas(body: str) -> tuple[str, List[str]]:
    """Tách các block $$...$$ khỏi phần diễn giải."""
    formulas = [m.strip() for m in _DISPLAY_FORMULA.findall(body) if m.strip()]
    explanation = _DISPLAY_FORMULA.sub("", body)
    explanation = re.sub(r"\n{3,}", "\n\n", explanation).strip()
    return explanation, formulas


def segment_steps(markdown: str) -> List[SolutionStep]:
    """
    Tách lời giải markdown thành các bước có cấu trúc cho canvas.

    Dùng cho response_format="steps". Chịu được markdown dở dang (đang stream):
    phần trước "Bước 1" gom vào bước "Phân tích đề", bước cuối có thể còn viết tiếp.
    """
    if not markdown.strip():
        return []

    intro: List[str] = []
    sections: List[dict] = []

    for line in markdown.split("\n"):
        match = _STEP_HEADING.match(line)
        if match:
            title = (match.group(2) or "").strip() or f"Bước {match.group(1)}"
            sections.append({"title": title, "body": []})
        elif sections:
            sections[-1]["body"].append(line)
        else:
            intro.append(line)

    steps: List[SolutionStep] = []

    def _add(title: str, body: str) -> None:
        explanation, formulas = _split_formulas(body)
        if not explanation and not formulas:
            return
        steps.append(
            {"index": len(steps), "title": title, "explanation": explanation, "formulas": formulas}
        )

    # Không có "Bước N" nào (bài ngắn, trắc nghiệm trả lời thẳng) -> 1 bước duy nhất.
    if not sections:
        _add("Lời giải", "\n".join(intro))
        return steps

    _add("Phân tích đề", "\n".join(intro))
    for i, section in enumerate(sections):
        body = section["body"]
        # Bước CUỐI thường dính thêm dòng chốt đáp án + mẹo nhớ (blockquote).
        # Tách ra thành bước "Kết luận" riêng để canvas nhấn mạnh đáp án.
        tail: List[str] = []
        if i == len(sections) - 1:
            cut = next(
                (j for j, line in enumerate(body) if _RESULT_LINE.match(line)),
                None,
            )
            if cut is not None:
                body, tail = body[:cut], body[cut:]

        _add(section["title"], "\n".join(body))
        if tail:
            _add("Kết luận", "\n".join(tail))

    return steps
