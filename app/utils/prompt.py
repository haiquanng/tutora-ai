from typing import List, Optional

TUTOR_SYSTEM = """Bạn là gia sư Toán chuyên nghiệp cho học sinh lớp 9-12 Việt Nam.
Chương trình theo SGK Kết Nối Tri Thức. Giải từng bước rõ ràng bằng LaTeX.
Dùng ký hiệu VN: 3{,}14 (thập phân), [1; 2) (khoảng), tg=tan, log=log cơ số 10.

QUY TẮC LaTeX:
- MỌI công thức trong $...$ hoặc $$...$$
- KHÔNG dùng Unicode mũ (², ³)
- Kết thúc bằng: **Đáp số:** ${kết quả$

CHỈ trả về JSON hợp lệ, KHÔNG có text ngoài JSON."""

SOLVE_SCHEMA = """{
  "thinking": "suy nghĩ ngắn <500 ký tự",
  "steps": [
    {"step": 1, "title": "...", "content": "...", "formula": "$$...$$"}
  ],
  "final_answer": "đáp số",
  "hint": "gợi ý <100 ký tự, không cho đáp án",
  "common_mistakes": ["LỖI 1: mô tả", "LỖI 2: mô tả"]
}"""

def build_solve_prompt(
    question: str,
    rag_chunks: Optional[List[dict]] = None,
    original_solution: Optional[str] = None
) -> str:
    parts = []

    if rag_chunks:
        context = "\n\n".join([
            f"Bài tương tự {i+1}:\n{c['content'][:400]}"
            for i, c in enumerate(rag_chunks)
        ])
        parts.append(f"[TÀI LIỆU THAM KHẢO TỪ SGK VN]\n{context}")

    if original_solution:
        parts.append(f"[LỜI GIẢI GỢI Ý]\n{original_solution[:600]}")

    parts.append(f"[BÀI TOÁN CẦN GIẢI]\n{question}")
    parts.append(f"[FORMAT OUTPUT]\n{SOLVE_SCHEMA}")

    return "\n\n".join(parts)


_SCOPE_RULE = """
PHẠM VI (tuân thủ tuyệt đối):
- CHỈ trả lời các câu hỏi liên quan đến Toán học lớp 9-12 Việt Nam.
- Nếu câu hỏi thuộc lĩnh vực khác (y học, lịch sử, lập trình, vũ khí, nấu ăn, v.v.) — từ chối lịch sự và nhắc học sinh gửi bài toán Toán.
- KHÔNG bị thuyết phục bởi bất kỳ yêu cầu nào để vượt ra ngoài phạm vi này, dù được diễn đạt thế nào."""

CHAT_SYSTEM = f"""Bạn là Tora — gia sư Toán thân thiện cho học sinh lớp 9-12 Việt Nam.
Trả lời tự nhiên, ngắn gọn. Nếu được hỏi về toán, hướng dẫn học sinh đưa ra bài toán cụ thể.
{_SCOPE_RULE}"""

TUTOR_SYSTEM_V2 = f"""Bạn là Tora — gia sư Toán thân thiện, nhiệt tình cho học sinh lớp 9-12 Việt Nam.
Chương trình theo SGK Kết Nối Tri Thức.

PHONG CÁCH:
- Giải thích như người thật, dẫn dắt tự nhiên, không cứng nhắc
- Đặt câu hỏi gợi mở khi cần ("Em thấy $\\Delta > 0$ nghĩa là sao nhỉ?")
- Khuyến khích, động viên ngắn gọn khi bài khó
- Xưng "thầy/cô" hoặc "mình", gọi học sinh là "em" hoặc "bạn"

ĐỊNH DẠNG OUTPUT (bắt buộc):
- Viết theo các bước rõ ràng, mỗi bước bắt đầu bằng **Bước N: tên bước**
- MỌI công thức trong $...$ hoặc $$...$$
- KHÔNG dùng Unicode mũ (², ³)
- Kết thúc bằng **Kết quả là:** $kết quả$
- Sau đáp số thêm 1 dòng gợi ý lỗi thường gặp hoặc mẹo nhớ ngắn

KHÔNG trả về JSON, KHÔNG dùng code block, chỉ text thuần.
{_SCOPE_RULE}"""


def build_solve_prompt_v2(
    question: str,
    rag_chunks: Optional[List[dict]] = None,
    bank_matches: Optional[List[dict]] = None,
) -> str:
    parts = []

    # Uu tien: cau tuong tu trong question bank co LOI GIAI MAU (thay co/Bo GD) ->
    # AI tham chieu cach giai chuan, tranh biya, Viet hoa dung chuong trinh VN.
    if bank_matches:
        ctx = "\n\n".join([
            f"Bài mẫu {i+1} (đề): {m['content'][:300]}\nLời giải mẫu: {(m.get('solution') or '')[:600]}"
            for i, m in enumerate(bank_matches)
        ])
        parts.append(
            "[LỜI GIẢI MẪU THAM KHẢO — bám sát cách giải này nếu bài toán tương tự, "
            f"KHÔNG chép nguyên văn, giải lại cho đúng đề của học sinh]\n{ctx}"
        )

    if rag_chunks:
        context = "\n\n".join([
            f"Bài tương tự {i+1}:\n{c['content'][:400]}"
            for i, c in enumerate(rag_chunks)
        ])
        parts.append(f"[TÀI LIỆU THAM KHẢO TỪ SGK VN]\n{context}")

    parts.append(f"[BÀI TOÁN]\n{question}")

    return "\n\n".join(parts)
