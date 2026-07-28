"""Danh sách slug chương trong prompt classifier.

Vì sao cần test: slug ở đây phải khớp CHÍNH XÁC cột chapters.slug bên .NET. Lệch một ký tự
thì LEFT JOIN miss -> tín hiệu học tập mất subject_id -> gợi ý gia sư sai môn, mà KHÔNG
có lỗi nào được ném ra. Đây là loại lỗi im lặng, chỉ phát hiện được bằng test.

Test này không chạm DB (CI không có kết nối). Nó khoá danh sách slug ở phía python;
đối chiếu với DB thật làm bằng scripts/check_chapter_slugs.py.
"""
import re

from app.services.homework.classifier import CLASSIFY_PROMPT

_BLOCK = CLASSIFY_PROMPT.split("DANH SÁCH CHAPTER HỢP LỆ")[1].split("LƯU Ý PHÂN BIỆT")[0]


def _slugs_of(text: str) -> list[str]:
    return [s for s in re.split(r"[,\s]+", text) if s and re.fullmatch(r"[a-z0-9_]+", s)]


# Chỉ lấy phần SAU mỗi nhãn [LỚP n] — câu dẫn phía trên cũng có từ tiếng Việt không dấu
# ("nhóm theo lớp") sẽ lọt vào nếu quét cả khối.
BY_GRADE: dict[str, list[str]] = {
    part.split("]")[0]: _slugs_of(part.split("]", 1)[1]) for part in _BLOCK.split("[LỚP ")[1:]
}
ALL_SLUGS = [s for slugs in BY_GRADE.values() for s in slugs]
SLUGS = set(ALL_SLUGS)


def test_co_du_slug_cho_bon_khoi_lop():
    assert set(BY_GRADE) == {"9", "10", "11", "12"}, f"Thiếu/thừa khối lớp: {set(BY_GRADE)}"
    # 11 + 12 + 11 + 10 = 44. Đổi số ở đây thì phải seed migration chapters tương ứng.
    assert len(SLUGS) == 44, f"Số slug thay đổi ({len(SLUGS)}); cập nhật cả migration chapters."


def test_slug_dung_dinh_dang():
    """Chữ thường + gạch dưới, không dấu — khớp ràng buộc cột chapters.slug."""
    for slug in SLUGS:
        assert re.fullmatch(r"[a-z0-9_]+", slug), f"Slug sai định dạng: {slug}"
        assert len(slug) <= 120, f"Slug dài quá 120 ký tự (giới hạn cột): {slug}"


def test_co_cac_chuong_lop_9_quan_trong():
    """
    Lớp 9 từng chỉ có 3 chương trong DB nên bài phổ biến nhất (phương trình bậc hai)
    không map được chương nào -> không sinh tín hiệu. Khoá lại để không tái diễn.
    """
    bat_buoc = {
        "phuong_trinh_bac_hai_mot_an",
        "can_bac_hai",
        "duong_tron",
        "he_thuc_luong_trong_tam_giac_vuong",
        "hinh_tru_hinh_non_hinh_cau",
    }
    assert bat_buoc <= SLUGS, f"Thiếu chương lớp 9: {bat_buoc - SLUGS}"


def test_khong_nham_hai_cap_slug_de_lan():
    """Hai cặp này khác lớp nhưng tên gần giống — phải cùng tồn tại, không thay thế nhau."""
    assert {"phuong_trinh_bac_hai_mot_an", "ham_so_bac_hai_va_do_thi"} <= SLUGS
    assert {"he_thuc_luong_trong_tam_giac_vuong", "he_thuc_luong_trong_tam_giac"} <= SLUGS


def test_khong_co_slug_trung():
    """Trùng slug làm model phân vân, và thường là dấu hiệu copy-paste sót."""
    assert len(ALL_SLUGS) == len(SLUGS), "Có slug bị lặp trong prompt classifier."
