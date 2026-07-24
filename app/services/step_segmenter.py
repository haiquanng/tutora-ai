import re
from typing import Iterator, List, Optional, TypedDict


class SolutionStep(TypedDict):
    index: int
    title: str
    explanation: str
    formulas: List[str]
    goal: str
    detailed: str
    hints: List[str]


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

_MARKER_LABELS = r"Vì sao|Kỹ hơn|Gợi ý\s*\d*"
_MARKER = re.compile(
    r"\*\*\s*(?P<label>" + _MARKER_LABELS + r")\s*:?\s*\*\*\s*:?\s*"
    r"(?P<body>.*?)"
    r"(?=\*\*\s*(?:" + _MARKER_LABELS + r")\s*:?\s*\*\*|\n|$)",
    re.IGNORECASE | re.DOTALL,
)


def _split_formulas(body: str) -> tuple[str, List[str]]:
    """Tách các block $$...$$ khỏi phần diễn giải."""
    formulas = [m.strip() for m in _DISPLAY_FORMULA.findall(body) if m.strip()]
    explanation = _DISPLAY_FORMULA.sub("", body)
    explanation = re.sub(r"\n{3,}", "\n\n", explanation).strip()
    return explanation, formulas


def _split_markers(body: str) -> tuple[str, str, str, List[str]]:
    """
    Bóc marker canvas (**Vì sao:** / **Kỹ hơn:** / **Gợi ý N:**) khỏi body — dù model
    viết chúng ĐẦU DÒNG hay INLINE giữa câu (Gemini hay nhét liền, xem ảnh bug 2026-07).

    Trả (body_còn_lại, goal, detailed, hints). Text ngoài marker giữ nguyên để explanation
    mặc định đọc liền mạch. Marker cắt ngang khi stream dở (chưa đủ "**") thì không khớp
    -> tạm nằm trong explanation, tới chunk sau đủ marker mới bóc ra.
    """
    goal = ""
    detailed = ""
    hints: List[str] = []

    def _capture(m: "re.Match[str]") -> str:
        nonlocal goal, detailed
        label = m.group("label").lower()
        text = m.group("body").strip()
        if not text:
            return ""
        if label.startswith("vì sao") and not goal:
            goal = text
        elif label.startswith("kỹ hơn") and not detailed:
            detailed = text
        elif label.startswith("gợi ý"):
            hints.append(text)
        return ""  # bóc khỏi explanation

    kept = _MARKER.sub(_capture, body)
    kept = re.sub(r"(?m)^[ \t]*[*+-][ \t]*$\n?", "", kept)
    # Dọn khoảng trắng thừa để lại sau khi cắt marker inline (vd " . " -> ".").
    kept = re.sub(r"[ \t]{2,}", " ", kept)
    kept = re.sub(r"\n{3,}", "\n\n", kept).strip()
    return kept, goal, detailed, hints


def segment_steps(markdown: str) -> List[SolutionStep]:
    """
    Tách NỘI DUNG CANVAS (đã bóc khỏi chat bởi _CanvasSplitter, xem solver_stream.py)
    thành các bước có cấu trúc. Chịu được markdown dở dang (đang stream): phần trước
    "Bước 1" gom vào bước "Phân tích đề", bước cuối có thể còn viết tiếp.
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
        # Bóc marker canvas TRƯỚC khi tách công thức: goal/hint có thể nằm cạnh $$...$$.
        body, goal, detailed, hints = _split_markers(body)
        explanation, formulas = _split_formulas(body)
        if not explanation and not formulas and not goal and not detailed and not hints:
            return
        steps.append(
            {
                "index": len(steps),
                "title": title,
                "explanation": explanation,
                "formulas": formulas,
                "goal": goal,
                "detailed": detailed,
                "hints": hints,
            }
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
