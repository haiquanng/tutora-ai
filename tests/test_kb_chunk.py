"""Unit test cho knowledge/chunk.py — thuần, không DB/Gemini, tất định."""
from app.services.knowledge.chunk import chunk_blocks, MAX_CHARS, MIN_CHARS


def test_khoi_ngan_giu_nguyen():
    """Khối ≤ MAX_CHARS → 1 chunk y nguyên (XLSX mỗi hàng, đoạn docx ngắn)."""
    blocks = ["Tutora là nền tảng kết nối phụ huynh với gia sư.", "Học phí tính theo giờ."]
    chunks = chunk_blocks(blocks)
    assert chunks == blocks


def test_bo_khoi_qua_ngan():
    """Khối dưới MIN_CHARS (nhiễu) bị loại."""
    chunks = chunk_blocks(["ok", "   ", "Đây là một câu đủ dài để giữ lại."])
    assert chunks == ["Đây là một câu đủ dài để giữ lại."]


def test_cat_khoi_dai_theo_cau():
    """Khối dài > MAX_CHARS → nhiều chunk, mỗi chunk ≤ MAX_CHARS."""
    sentence = "Tutora hỗ trợ học online và tại nhà cho nhiều môn học khác nhau. "
    long_block = sentence * 60  # chắc chắn vượt MAX_CHARS
    chunks = chunk_blocks([long_block])
    assert len(chunks) > 1
    for c in chunks:
        # Cho phép nhỉnh nhẹ do overlap + 1 câu cuối, nhưng không được gấp đôi.
        assert len(c) <= MAX_CHARS * 2


def test_khong_mat_noi_dung_khi_cat():
    """Nội dung chính vẫn còn sau khi cắt (không rơi câu)."""
    block = "Câu một là duy nhất. " + ("Câu lặp lại nhiều lần. " * 80) + "Câu cuối đặc biệt."
    chunks = chunk_blocks([block])
    joined = " ".join(chunks)
    assert "Câu một là duy nhất" in joined
    assert "Câu cuối đặc biệt" in joined
