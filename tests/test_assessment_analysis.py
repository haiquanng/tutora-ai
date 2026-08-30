"""Chuẩn hoá output Gemini của /analyze-assessment.

Không gọi model thật: nạp thẳng JSON thô vào analyzer._coerce. Mấy ca ở đây là
dữ liệu HỎNG THẬT từng làm FE trắng trang (verdict 'weak' -> index bảng màu bằng
key không tồn tại), nên giữ lại làm hồi quy.
"""
import pytest

from app.models.assessment import AnalysisInput, ChapterStat
from app.services.assessment.analyzer import _coerce


def _inp(*chapters: str) -> AnalysisInput:
    return AnalysisInput(
        attempt_id="att-1",
        chapter_stats=[ChapterStat(chapter_name=c, chapter_slug=c.lower(), total=3, correct=1) for c in chapters],
    )


def _mastery(**over):
    row = {"chapter": "Đạo hàm", "chapterSlug": "dao-ham", "correct": 1, "total": 3, "verdict": "gap"}
    row.update(over)
    return row


def test_giu_nguyen_output_hop_le():
    raw = {
        "level": "developing",
        "summary": "Bạn làm tốt phần nhận biết.",
        "confidence": "medium",
        "strengths": [{"chapter": "Đạo hàm", "chapterSlug": "dao-ham", "note": "Nắm định nghĩa"}],
        "weaknesses": [{"chapter": "Đạo hàm", "chapterSlug": "dao-ham", "severity": "moderate", "note": "Sai quy tắc"}],
        "chapterMastery": [_mastery(improve=[{"title": "Đạo hàm hàm hợp", "why": "Gỡ đúng lỗi"}])],
        "recommendedPath": [{"order": 1, "chapter": "Đạo hàm", "goal": "Thuộc quy tắc", "practice": ["Hàm hợp"]}],
        "nextAction": "Luyện 5 câu hàm hợp.",
    }
    out = _coerce(raw, _inp("Đạo hàm"))

    assert out["level"] == "developing"
    assert out["confidence"] == "medium"
    assert out["chapter_mastery"][0]["verdict"] == "gap"
    assert out["chapter_mastery"][0]["improve"][0]["title"] == "Đạo hàm hàm hợp"
    assert out["recommended_path"][0]["practice"] == ["Hàm hợp"]
    assert out["next_action"] == "Luyện 5 câu hàm hợp."


def test_camel_case_cho_net_va_fe():
    """chapterSlug/estimatedSessions phải giữ camelCase — FE đọc đúng key này."""
    raw = {
        "chapterMastery": [_mastery()],
        "recommendedPath": [{"order": 1, "chapter": "Đạo hàm", "chapterSlug": "dao-ham", "estimatedSessions": 2}],
    }
    out = _coerce(raw, _inp("Đạo hàm"))

    assert out["chapter_mastery"][0]["chapterSlug"] == "dao-ham"
    assert out["recommended_path"][0]["estimatedSessions"] == 2


@pytest.mark.parametrize("bad", ["weak", "WEAK", "siêu vững", "", None, 5, []])
def test_verdict_la_bi_bo_khong_lam_no(bad):
    """Verdict ngoài solid/shaky/gap -> None, FE tự suy từ correct/total."""
    out = _coerce({"chapterMastery": [_mastery(verdict=bad)]}, _inp("Đạo hàm"))

    assert len(out["chapter_mastery"]) == 1
    assert out["chapter_mastery"][0]["verdict"] is None


@pytest.mark.parametrize("bad", ["GIOI", "beginner ", "", None, 3])
def test_level_la_ve_none(bad):
    """.NET reject level lạ -> để None cho BE giữ mức cũ của profile."""
    assert _coerce({"level": bad}, _inp())["level"] is None


@pytest.mark.parametrize("bad", ["rất cao", "HIGH ", "", None])
def test_confidence_la_ve_none(bad):
    assert _coerce({"confidence": bad}, _inp())["confidence"] is None


def test_severity_la_ve_none_van_giu_chuong():
    out = _coerce({"weaknesses": [{"chapter": "Đạo hàm", "severity": "cực nặng"}]}, _inp("Đạo hàm"))

    assert len(out["weaknesses"]) == 1
    assert out["weaknesses"][0]["severity"] is None


def test_mang_thanh_object_khong_lam_no():
    """AI trả object thay mảng -> mảng rỗng, không vỡ."""
    raw = {"strengths": {"chapter": "Đạo hàm"}, "recommendedPath": "chưa có", "chapterMastery": None}
    out = _coerce(raw, _inp("Đạo hàm"))

    assert out["strengths"] == []
    assert out["recommended_path"] == []
    assert out["chapter_mastery"] == []


def test_bo_muc_hong_giu_muc_lanh():
    """Một mục hỏng KHÔNG được kéo theo cả mảng — học sinh vẫn thấy phần còn lại."""
    raw = {"chapterMastery": [_mastery(), None, "chuỗi lạc", {"note": "thiếu tên chương"}, _mastery(chapter="Tích phân")]}
    out = _coerce(raw, _inp("Đạo hàm", "Tích phân"))

    assert [r["chapter"] for r in out["chapter_mastery"]] == ["Đạo hàm", "Tích phân"]


def test_so_dang_chuoi_duoc_ep_ve_so():
    out = _coerce({"chapterMastery": [_mastery(correct="2", total="3")]}, _inp("Đạo hàm"))
    row = out["chapter_mastery"][0]

    assert (row["correct"], row["total"]) == (2, 3)


def test_chan_model_bia_chuong():
    """Chương không có trong đề -> bỏ, kể cả khi mọi field đều hợp lệ."""
    raw = {"chapterMastery": [_mastery(), _mastery(chapter="Số phức")]}
    out = _coerce(raw, _inp("Đạo hàm"))

    assert [r["chapter"] for r in out["chapter_mastery"]] == ["Đạo hàm"]


def test_de_chua_gan_chuong_thi_khong_loc():
    raw = {"chapterMastery": [_mastery(chapter="Chương lạ")]}
    out = _coerce(raw, _inp())

    assert len(out["chapter_mastery"]) == 1


def test_json_rong_ra_output_rong_khong_raise():
    out = _coerce({}, _inp("Đạo hàm"))

    assert out["level"] is None
    assert out["summary"] == ""
    assert out["chapter_mastery"] == []
    assert out["next_action"] is None


def test_field_sai_kieu_khong_keo_sap_ca_khoi():
    """summary sai kiểu làm hỏng validate toàn khối -> vẫn phải giữ được chapterMastery."""
    raw = {"summary": 42, "chapterMastery": [_mastery()]}
    out = _coerce(raw, _inp("Đạo hàm"))

    assert out["summary"] == ""
    assert len(out["chapter_mastery"]) == 1


def test_practice_lan_phan_tu_khong_phai_chuoi():
    """FE dùng chính phần tử practice làm React key -> phải là chuỗi."""
    raw = {"recommendedPath": [{"order": 1, "chapter": "Đạo hàm", "practice": ["Hàm hợp", 5, None]}]}
    out = _coerce(raw, _inp("Đạo hàm"))

    assert all(isinstance(p, str) for p in out["recommended_path"][0]["practice"])


def test_improve_hong_khong_lam_mat_ca_chuong():
    raw = {"chapterMastery": [_mastery(improve=[{"why": "thiếu title"}, {"title": "Hàm hợp"}, None])]}
    out = _coerce(raw, _inp("Đạo hàm"))

    assert len(out["chapter_mastery"]) == 1
    assert [i["title"] for i in out["chapter_mastery"][0]["improve"]] == ["Hàm hợp"]
