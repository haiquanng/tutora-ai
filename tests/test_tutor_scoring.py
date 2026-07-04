"""Unit test cho score_tutor (Pha 3 ranking) — thuần, không gọi DB/Gemini, tất định."""
from app.services.tutor_matching import score_tutor, _bayesian_rating


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
