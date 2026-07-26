"""
Cắt khối text thô thành CHUNK để embed.

Nguyên tắc RAG (giống comment kb_tutora.py): mỗi chunk TỰ ĐỦ NGHĨA, không quá dài
(vector loãng, tìm không trúng) cũng không quá ngắn (thiếu ngữ cảnh).

Chiến lược:
  • Khối ngắn (≤ MAX_CHARS): giữ nguyên 1 chunk (XLSX mỗi hàng, docx đoạn ngắn).
  • Khối dài (văn xuôi PDF/docx): cắt cửa sổ trượt theo CÂU, chồng lấn OVERLAP để
    không đứt ý giữa chừng.
"""
from __future__ import annotations

import re

# ~ giới hạn ký tự/chunk (xấp xỉ 300-400 từ tiếng Việt). Cân bằng ngữ cảnh vs độ sắc của vector.
MAX_CHARS = 1200
# Chồng lấn giữa 2 chunk liền — giữ ngữ cảnh bắc cầu, không đứt ý ở ranh giới.
OVERLAP_CHARS = 150
# Chunk quá ngắn (nhiễu) — bỏ.
MIN_CHARS = 20

_SENTENCE_SPLIT = re.compile(r"(?<=[.!?…。])\s+")


def chunk_blocks(blocks: list[str]) -> list[str]:
    """Nhiều khối thô → list chunk cuối cùng (đã lọc rỗng/quá ngắn)."""
    chunks: list[str] = []
    for block in blocks:
        block = block.strip()
        if len(block) <= MAX_CHARS:
            if len(block) >= MIN_CHARS:
                chunks.append(block)
            continue
        chunks.extend(_split_long(block))
    return chunks


def _split_long(text: str) -> list[str]:
    """Cắt 1 khối dài theo câu, gom tới ~MAX_CHARS, chồng lấn OVERLAP_CHARS."""
    sentences = _SENTENCE_SPLIT.split(text)
    out: list[str] = []
    cur = ""
    for s in sentences:
        s = s.strip()
        if not s:
            continue
        if cur and len(cur) + len(s) + 1 > MAX_CHARS:
            out.append(cur.strip())
            # Bắt đầu chunk sau bằng phần đuôi (overlap) của chunk trước.
            tail = cur[-OVERLAP_CHARS:] if len(cur) > OVERLAP_CHARS else cur
            cur = (tail + " " + s).strip()
        else:
            cur = (cur + " " + s).strip() if cur else s
    if len(cur) >= MIN_CHARS:
        out.append(cur.strip())
    return out
