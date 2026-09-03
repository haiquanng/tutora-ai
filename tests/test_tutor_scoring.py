"""Unit test cho score_tutor (Pha 3 ranking) — thuần, không gọi DB/Gemini, tất định."""
from app.services.tutoring_shared.matching import score_tutor, _bayesian_rating


def test_bayesian_keo_rating_it_review_ve_trung_binh():
    """1 review 5 sao KHÔNG được thắng 500 review 4.8 sao (bug của average_rating thô)."""
    one_review = _bayesian_rating(average_rating=5.0, total_reviews=1)
    many_reviews = _bayesian_rating(average_rating=4.8, total_reviews=500)
    assert many_reviews > one_review


def test_zero_review_khong_bang_khong():
    """Gia sư 0 review phải được coi như 'chưa biết' (~trung bình hệ thống), KHÔNG phải rating=0."""
    zero_review = _bayesian_rating(average_rating=None, total_reviews=0)
    assert zero_review > 3.0, "gia sư mới bị coi như rating=0, sẽ bị chôn vĩnh viễn"


def test_khong_query_gia_su_moi_khong_bi_chon_day():
    """Nhánh không-query: gia sư mới (0 review) vẫn có điểm > 0, không tuyệt đối thua mọi
    gia sư có review thấp/tệ."""
    new_tutor = score_tutor(average_rating=None, total_reviews=0, completed_hours=0)
    bad_established = score_tutor(average_rating=2.0, total_reviews=100, completed_hours=200)
    assert new_tutor > bad_established, "gia sư mới thua cả gia sư established nhưng rating tệ"


def test_co_query_similarity_cao_thang_neu_rating_ngang_nhau():
    high_sim = score_tutor(similarity=0.9, average_rating=4.5, total_reviews=20, completed_hours=100)
    low_sim = score_tutor(similarity=0.3, average_rating=4.5, total_reviews=20, completed_hours=100)
    assert high_sim > low_sim


def test_co_query_rating_cao_van_co_the_thang_similarity_thap_hon_chut():
    """Blend nghĩa là rating tốt có thể bù lại similarity kém hơn 1 chút — không phải
    similarity quyết định tuyệt đối 100% (đây chính là lý do cần Pha 3, không chỉ sort
    theo similarity thuần)."""
    great_rating_ok_sim = score_tutor(similarity=0.6, average_rating=5.0, total_reviews=200, completed_hours=1000)
    perfect_sim_no_track_record = score_tutor(similarity=0.65, average_rating=None, total_reviews=0, completed_hours=0)
    assert great_rating_ok_sim > perfect_sim_no_track_record


def test_experience_score_khong_am_va_bounded():
    assert score_tutor(average_rating=4.5, total_reviews=10, completed_hours=0) > 0
    assert score_tutor(average_rating=4.5, total_reviews=10, completed_hours=10_000) <= 1.0


# ─────────── Chuẩn hoá similarity trong pool ───────────

def test_khop_ngu_nghia_vuot_troi_khong_bi_kinh_nghiem_dim():
    """Bug thật 2026-09-02: user tìm "tốt nghiệp Nottingham, dạy ở UK/Trung Quốc".
    Người DUY NHẤT đúng hồ sơ có similarity 0.807 (cao nhất pool) nhưng 0 giờ dạy, thua
    người similarity 0.712 có 300 giờ — xếp CUỐI, không được gợi ý.

    Nguyên nhân: cosine nằm dải hẹp (~0.65–0.85) còn rating/kinh nghiệm trải gần trọn
    0–1, đem so trực tiếp là so hai thang khác nhau.
    """
    # Điểm với similarity THÔ — tái hiện lỗi.
    khanh_raw = score_tutor(similarity=0.8071, average_rating=0, total_reviews=0,
                            completed_hours=0, specific_query=True)
    thuy_raw = score_tutor(similarity=0.7117, average_rating=4.1, total_reviews=55,
                           completed_hours=300, specific_query=True)
    assert khanh_raw < thuy_raw, "tái hiện: similarity thô thì người khớp nhất vẫn thua"

    # Sau khi chuẩn hoá trong pool (0.807 -> 1.0, 0.712 -> 0.0).
    khanh_norm = score_tutor(similarity=1.0, average_rating=0, total_reviews=0,
                             completed_hours=0, specific_query=True)
    thuy_norm = score_tutor(similarity=0.0, average_rating=4.1, total_reviews=55,
                            completed_hours=300, specific_query=True)
    assert khanh_norm > thuy_norm, "khớp vượt trội phải thắng"


def test_pool_khong_phan_biet_duoc_thi_nhuong_chat_luong():
    """Spread nhỏ = ai cũng na ná; chuẩn hoá lúc đó chỉ khuếch đại nhiễu embedding thành
    0 vs 1. Khi đó cả pool nhận cùng một giá trị, để rating/kinh nghiệm quyết."""
    from app.services.tutoring_shared.matching import _SIM_SPREAD_MIN

    assert _SIM_SPREAD_MIN > 0
    moi = score_tutor(similarity=0.5, average_rating=0, total_reviews=0,
                      completed_hours=0, specific_query=True)
    ky_cuu = score_tutor(similarity=0.5, average_rating=4.8, total_reviews=120,
                         completed_hours=500, specific_query=True)
    assert ky_cuu > moi
