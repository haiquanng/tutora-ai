"""Phân tích bài đánh giá đầu vào -> profile trình độ + lộ trình học.

BE chấm khách quan rồi gửi TRỌN dữ kiện thô qua đây; AI là chỗ duy nhất kết luận
trình độ. Không có ngưỡng đạt/không đạt — level suy từ chất lượng làm bài theo
chương + độ khó, không phải từ mốc điểm.
"""
import json
import logging
from typing import Any

from google.genai import types
from pydantic import BaseModel, ValidationError

from ...models.assessment import (
    AnalysisInput,
    AnalysisOutput,
    AnalysisSchema,
    ChapterMastery,
    ChapterNote,
    PathStep,
    WeaknessNote,
)

logger = logging.getLogger(__name__)

_MODEL = "gemini-2.5-flash"


_DIFFICULTY_VI = {
    "NHAN_BIET": "Nhận biết",
    "THONG_HIEU": "Thông hiểu",
    "VAN_DUNG": "Vận dụng",
    "VAN_DUNG_CAO": "Vận dụng cao",
}

SYSTEM = """Bạn là giáo viên khảo thí, phân tích bài đánh giá đầu vào của học sinh Việt Nam
để xây lộ trình học cá nhân hoá.

NGUYÊN TẮC:
- KHÔNG có ngưỡng đạt/không đạt. Không dùng từ "đạt", "không đạt", "trượt", "yếu kém".
- Giọng động viên nhưng TRUNG THỰC: chỉ đúng lỗ hổng, không tô hồng, không phán xét.
- Bám DỮ KIỆN được cho. Không suy diễn về chương không xuất hiện trong đề.
- Phân biệt BỎ TRỐNG (chưa biết làm / hết thời gian) với TRẢ LỜI SAI (hiểu sai bản chất).
- Câu tự luận (essay) BE không tự chấm: tự đối chiếu bài làm với đáp án mẫu rồi kết luận.
- Đề chỉ vài câu/chương thì nói rõ độ tin cậy còn thấp, đừng kết luận chắc chắn.

CÁCH CHỌN level:
- beginner: sai/bỏ trống phần lớn câu Nhận biết — hổng kiến thức nền.
- developing: làm được Nhận biết, chật vật từ Thông hiểu trở lên.
- proficient: vững tới Vận dụng, chỉ rơi ở Vận dụng cao.
- advanced: làm tốt cả Vận dụng cao.

CHỈ trả về JSON hợp lệ, không markdown fence, không text ngoài JSON."""

SCHEMA = """{
  "level": "beginner | developing | proficient | advanced",
  "summary": "2-4 câu markdown nói với học sinh (ngôi 'bạn'): làm được gì, hổng ở đâu, nên bắt đầu từ đâu",
  "confidence": "low | medium | high — độ tin cậy của kết luận, dựa vào số câu/chương của đề",
  "strengths": [
    {"chapter": "tên chương", "chapterSlug": "slug hoặc null", "note": "1 câu vì sao coi là điểm mạnh"}
  ],
  "weaknesses": [
    {"chapter": "tên chương", "chapterSlug": "slug hoặc null",
     "severity": "minor | moderate | critical",
     "note": "1 câu chỉ rõ hiểu sai/thiếu gì, dẫn chứng từ câu cụ thể"}
  ],
  "chapterMastery": [
    {"chapter": "tên chương", "chapterSlug": "slug hoặc null",
     "correct": số, "total": số,
     "verdict": "solid | shaky | gap",
     "summary": "2-3 câu: học sinh hiểu/chưa hiểu gì ở chương này, dẫn chứng từ câu cụ thể",
     "improve": [
       {"title": "tên dạng bài nên luyện",
        "why": "1 câu vì sao dạng này gỡ đúng lỗ hổng đang có"}
     ]}
  ],
  "recommendedPath": [
    {"order": 1, "chapter": "tên chương", "chapterSlug": "slug hoặc null",
     "goal": "học xong bước này làm được gì",
     "why": "1 câu vì sao xếp bước này ở đây",
     "practice": ["dạng bài cụ thể nên luyện", "..."],
     "estimatedSessions": số buổi ước lượng}
  ],
  "nextAction": "1 câu việc nên làm NGAY hôm nay"
}

RÀNG BUỘC:
- chapterMastery phải có ĐỦ mọi chương xuất hiện trong [THỐNG KÊ THEO CHƯƠNG], không thêm chương lạ.
- correct/total lấy ĐÚNG số đã cho, không tự đếm lại.
- TUYỆT ĐỐI KHÔNG cho điểm phần trăm hay thang số cho mức thông thạo: chỉ dùng verdict
  (solid = đã vững / shaky = chưa chắc / gap = đang hổng). Đề ít câu nên % gây cảm giác
  chính xác giả (2/3 câu không có nghĩa là "thông thạo 67%").
- verdict xét theo CHẤT của lỗi, không theo tỷ lệ đúng: sai/bỏ câu Nhận biết -> gap dù
  các câu khác đúng; chỉ rơi câu Vận dụng cao -> vẫn có thể solid.
- improve: 1-3 dạng bài mỗi chương, cụ thể tới mức học sinh biết luyện gì ngay
  (vd "Rút gọn biểu thức chứa căn bậc hai"), KHÔNG nói chung như "ôn lại lý thuyết".
- recommendedPath xếp theo thứ tự HỌC: chương hổng nặng và là nền tảng đứng trước.
- recommendedPath 2-5 bước. Chỉ đưa chương có trong đề.
- chapterSlug copy nguyên từ dữ kiện, dùng để truy vấn bài tập; không có thì để null."""


def _fmt_items(inp: AnalysisInput) -> str:
    lines = []
    for it in inp.items:
        if it.skipped:
            answer = "BỎ TRỐNG"
        else:
            answer = f'"{it.given_answer}"'
        verdict = "ĐÚNG" if it.is_correct else "SAI"
        # essay: BE không chấm nên is_correct luôn False -> nói rõ để AI tự đánh giá.
        if it.question_format == "essay":
            verdict = "CHƯA CHẤM (tự luận — bạn tự đối chiếu)"

        diff = _DIFFICULTY_VI.get(it.difficulty or "", it.difficulty or "?")
        time = f", {it.time_spent_seconds}s" if it.time_spent_seconds else ""
        lines.append(
            f"Câu {it.display_order} [{it.chapter_name or 'chưa gắn chương'} | {diff} "
            f"| {it.question_format}{time}]\n"
            f"  Đề: {it.content[:400]}\n"
            f"  Đáp án đúng: {it.correct_answer[:300]}\n"
            f"  Học sinh trả lời: {answer} -> {verdict}"
        )
    return "\n\n".join(lines)


def _fmt_chapters(inp: AnalysisInput) -> str:
    if not inp.chapter_stats:
        return "(đề chưa gắn chương)"
    return "\n".join(
        f"- {c.chapter_name or 'chưa gắn chương'} (slug: {c.chapter_slug or 'null'}): "
        f"đúng {c.correct}/{c.total}, bỏ trống {c.skipped}"
        for c in inp.chapter_stats
    )


def _fmt_difficulty(inp: AnalysisInput) -> str:
    if not inp.difficulty_stats:
        return "(đề chưa gắn độ khó)"
    return "\n".join(
        f"- {_DIFFICULTY_VI.get(d.difficulty or '', d.difficulty or '?')}: "
        f"đúng {d.correct}/{d.total}, bỏ trống {d.skipped}"
        for d in inp.difficulty_stats
    )


def build_prompt(inp: AnalysisInput) -> str:
    attempt_note = (
        f"Đây là lần đánh giá thứ {inp.attempt_count} của học sinh với môn này."
        if inp.attempt_count > 1
        else "Đây là lần đánh giá ĐẦU TIÊN của học sinh với môn này."
    )
    score = (
        f"{inp.earned_points}/{inp.max_points} điểm"
        f"{f' ({inp.score_percent}%)' if inp.score_percent is not None else ''}"
    )
    duration = f"{inp.duration_seconds}s" if inp.duration_seconds else "không rõ"

    return "\n\n".join([
        "[BỐI CẢNH]\n"
        f"Môn: {inp.subject_name or '?'} — Lớp: {inp.grade_name or '?'}\n"
        f"Đề: {inp.assessment_title}\n"
        f"{attempt_note}",

        "[KẾT QUẢ TỔNG]\n"
        f"Đúng {inp.correct_count}/{inp.total_questions} câu, {score}. "
        f"Thời gian làm: {duration}.",

        f"[THỐNG KÊ THEO CHƯƠNG]\n{_fmt_chapters(inp)}",
        f"[THỐNG KÊ THEO ĐỘ KHÓ]\n{_fmt_difficulty(inp)}",
        f"[CHI TIẾT TỪNG CÂU]\n{_fmt_items(inp)}",
        f"[FORMAT OUTPUT]\n{SCHEMA}",
    ])


def _strip_fence(text: str) -> str:
    """Model đôi khi bọc ```json dù đã bảo đừng."""
    t = text.strip()
    if t.startswith("```"):
        t = t.split("\n", 1)[1] if "\n" in t else t[3:]
        if t.rstrip().endswith("```"):
            t = t.rstrip()[:-3]
    return t.strip()


def _validate(raw: dict[str, Any], inp: AnalysisInput) -> AnalysisSchema:
    """Ép output model về đúng schema
    """
    try:
        return AnalysisSchema.model_validate(raw)
    except ValidationError as e:
        logger.warning(
            "AI trả JSON lệch schema cho attempt %s (%d lỗi) — lọc từng mục.",
            inp.attempt_id, e.error_count(),
        )

    def _rows(model: type[BaseModel], raw_rows: Any) -> list[Any]:
        """Validate từng phần tử, bỏ phần tử hỏng thay vì bỏ cả mảng."""
        if not isinstance(raw_rows, list):
            return []
        out = []
        for row in raw_rows:
            try:
                out.append(model.model_validate(row))
            except ValidationError:
                logger.warning("Bỏ 1 mục %s hỏng (attempt %s): %r", model.__name__, inp.attempt_id, row)
        return out

    # level/confidence: enum lenient ở model đã lo, truyền thẳng.
    return AnalysisSchema(
        level=raw.get("level"),
        summary=raw.get("summary") if isinstance(raw.get("summary"), str) else "",
        confidence=raw.get("confidence"),
        strengths=_rows(ChapterNote, raw.get("strengths")),
        weaknesses=_rows(WeaknessNote, raw.get("weaknesses")),
        chapterMastery=_rows(ChapterMastery, raw.get("chapterMastery")),
        recommendedPath=_rows(PathStep, raw.get("recommendedPath")),
        nextAction=raw.get("nextAction") if isinstance(raw.get("nextAction"), str) else "",
    )


def _coerce(raw: dict[str, Any], inp: AnalysisInput) -> dict[str, Any]:
    """Validate + chuẩn hoá output model về đúng chuẩn .NET.

    Level lạ -> None: .NET giữ mức cũ của profile thay vì ghi giá trị DB reject.
    """
    data = _validate(raw, inp)

    if data.level is None:
        logger.warning("AI không trả level dùng được cho attempt %s — bỏ.", inp.attempt_id)

    # Chỉ giữ chương thật có trong đề — chặn model bịa chương.
    known = {c.chapter_name for c in inp.chapter_stats if c.chapter_name}

    def _dump(rows: list[Any]) -> list[dict]:
        # Không có chương nào gắn thì thôi không lọc (đề chưa gắn chương).
        keep = [r for r in rows if not known or r.chapter in known]
        # by_alias: FE/.NET đọc camelCase (chapterSlug, estimatedSessions).
        return [r.model_dump(by_alias=True, mode="json") for r in keep]

    return {
        "level": data.level.value if data.level else None,
        "summary": data.summary,
        "confidence": data.confidence.value if data.confidence else None,
        "strengths": _dump(data.strengths),
        "weaknesses": _dump(data.weaknesses),
        "chapter_mastery": _dump(data.chapterMastery),
        "recommended_path": _dump(data.recommendedPath),
        "next_action": data.nextAction or None,
    }


async def analyze(client, inp: AnalysisInput) -> AnalysisOutput:
    """Gọi Gemini 1 lượt, trả kết quả đã chuẩn hoá. Lỗi parse -> raise cho router."""
    cfg = types.GenerateContentConfig(
        temperature=0.3,
        system_instruction=SYSTEM,
        response_mime_type="application/json",
        # Ràng buộc chính model chỉ sinh được đúng hình dạng + đúng enum
        response_schema=AnalysisSchema,
    )

    response = await client.aio.models.generate_content(
        model=_MODEL,
        contents=build_prompt(inp),
        config=cfg,
    )

    text = _strip_fence(response.text or "")
    if not text:
        raise ValueError("Model trả về rỗng")

    raw = json.loads(text)
    if not isinstance(raw, dict):
        raise ValueError("Model không trả JSON object")

    return AnalysisOutput(**_coerce(raw, inp))
