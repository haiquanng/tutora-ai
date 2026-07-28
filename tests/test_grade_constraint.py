"""Ràng buộc trình độ trong prompt giải bài.

Vì sao cần: không có ràng buộc lớp, model hay giải bài lớp 9 bằng đạo hàm — đúng đáp số
nhưng học sinh chưa học nên đọc không hiểu. Đây chính là điểm khác biệt so với chatbot
đại trà (vốn không biết người hỏi học lớp mấy), nên phải khoá bằng test.
"""
import pytest

from app.utils.prompt import build_solve_prompt_v2


def test_lop_9_cam_dao_ham():
    prompt = build_solve_prompt_v2("Giải phương trình x^2 - 5x + 6 = 0", grade="9")

    assert "LỚP 9" in prompt
    assert "KHÔNG dùng đạo hàm" in prompt


def test_lop_12_duoc_dung_toan_bo():
    prompt = build_solve_prompt_v2("Tính tích phân", grade="12")

    assert "LỚP 12" in prompt
    assert "KHÔNG dùng đạo hàm" not in prompt


def test_thi_vao_10_dung_kien_thuc_thcs():
    prompt = build_solve_prompt_v2("Bài hình học", grade="thi_vao_10")

    assert "THCS" in prompt


@pytest.mark.parametrize("grade", [None, "", "lop_9", "unknown"])
def test_grade_thieu_hoac_la_thi_khong_them_rang_buoc(grade):
    """Không biết lớp thì im lặng, KHÔNG đoán bừa một ràng buộc sai."""
    prompt = build_solve_prompt_v2("Giải phương trình", grade=grade)

    assert "TRÌNH ĐỘ HỌC SINH" not in prompt
    assert "[BÀI TOÁN]" in prompt


def test_de_bai_luon_o_cuoi_prompt():
    """Ràng buộc lớp chèn lên đầu không được đẩy đề bài khỏi vị trí cuối."""
    prompt = build_solve_prompt_v2(
        "Giải phương trình x + 1 = 0",
        rag_chunks=[{"content": "bài mẫu nào đó"}],
        grade="10",
    )

    assert prompt.index("[TRÌNH ĐỘ HỌC SINH]") < prompt.index("[BÀI TOÁN]")
    assert prompt.rstrip().endswith("Giải phương trình x + 1 = 0")
