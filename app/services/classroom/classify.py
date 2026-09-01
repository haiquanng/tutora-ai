"""
Đối chiếu nội dung tài liệu với MÔN gia sư đang dạy.
"""
from __future__ import annotations

import asyncio
import json

from google.genai import types

MODEL = "gemini-2.5-flash"

# Chỉ cần vài nghìn ký tự đầu là đủ nhận ra tài liệu nói về cái gì. Gửi cả file vừa
# chậm vừa tốn, mà không chính xác hơn.
_SAMPLE_CHARS = 4000

_PROMPT = """Bạn kiểm tra xem một tài liệu có phải là học liệu của MÔN dưới đây không.

MÔN HỌC: <<SUBJECT>>

TRÍCH ĐOẠN TÀI LIỆU:
<<SAMPLE>>

Trả lời:
- "relevant": true nếu tài liệu là HỌC LIỆU của môn trên (lý thuyết, bài tập, đề thi,
  slide bài giảng, ghi chép bài học, ảnh chụp trang sách/vở của môn đó).
- "relevant": false nếu KHÔNG phải học liệu của môn này. Ví dụ: CV/hồ sơ xin việc,
  hợp đồng, hoá đơn, ảnh chụp màn hình chat, tài liệu của môn khác hẳn.

Nguyên tắc:
- TUYỆT ĐỐI KHÔNG xét khối lớp. Tài liệu lớp nào cũng được, lớp trên ôn lại kiến
  thức lớp dưới là chuyện bình thường. Chỉ xét ĐÚNG MÔN hay không.
- Nếu phân vân, hãy cho qua (relevant = true) — chặn nhầm học liệu thật gây khó chịu
  hơn là lọt một tài liệu hơi lệch.
- "reason": nếu false, nói NGẮN GỌN tài liệu này là gì, để gia sư hiểu vì sao bị từ
  chối. Ví dụ: "Đây là CV xin việc, không phải học liệu môn Toán." Không được nhắc
  tới khối lớp trong lý do.

Trả về JSON đúng schema."""

_SCHEMA = {
    "type": "OBJECT",
    "properties": {
        "relevant": {"type": "BOOLEAN"},
        "reason": {"type": "STRING"},
    },
    "required": ["relevant"],
}


async def check_relevance(gemini, full_text: str, subject: str) -> dict:
    """Trả {relevant: bool, reason: str|None}.

    Lỗi gọi model -> CHO QUA (relevant=True): dịch vụ AI trục trặc không được biến
    thành chặn gia sư tải tài liệu.
    """
    sample = (full_text or "").strip()[:_SAMPLE_CHARS]
    if not sample:
        return {"relevant": False, "reason": "Tài liệu không có nội dung chữ đọc được."}

    prompt = _PROMPT.replace("<<SUBJECT>>", subject or "(không rõ)").replace(
        "<<SAMPLE>>", sample
    )

    try:
        response = await asyncio.to_thread(
            gemini.models.generate_content,
            model=MODEL,
            contents=prompt,
            config=types.GenerateContentConfig(
                temperature=0.0,
                response_mime_type="application/json",
                response_schema=_SCHEMA,
            ),
        )
        raw = json.loads(response.text)
    except Exception as e:
        print(f"check_relevance error: {e}")
        return {"relevant": True, "reason": None}

    return {
        "relevant": bool(raw.get("relevant", True)),
        "reason": (raw.get("reason") or "").strip() or None,
    }
