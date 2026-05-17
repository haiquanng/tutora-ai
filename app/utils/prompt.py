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

    return "\n\n---\n\n".join(parts)


TUTOR_SYSTEM_V2 = """Bạn là Tora — gia sư Toán thân thiện, nhiệt tình cho học sinh lớp 9-12 Việt Nam.
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

KHÔNG trả về JSON, KHÔNG dùng code block, chỉ text thuần."""


def build_solve_prompt_v2(
    question: str,
    rag_chunks: Optional[List[dict]] = None,
) -> str:
    parts = []

    if rag_chunks:
        context = "\n\n".join([
            f"Bài tương tự {i+1}:\n{c['content'][:400]}"
            for i, c in enumerate(rag_chunks)
        ])
        parts.append(f"[TÀI LIỆU THAM KHẢO TỪ SGK VN]\n{context}")

    parts.append(f"[BÀI TOÁN]\n{question}")

    return "\n\n---\n\n".join(parts)
