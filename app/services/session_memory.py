"""
Tóm tắt phiên chat cũ khi user quay lại sau gap dài (session memory).

BÀI TOÁN: user chat dở việc tìm gia sư, ngưng, quay lại sau vài tiếng gõ "Hola".
Nếu nối thẳng history cũ, agent tưởng đang tiếp tục -> bắn lại gia sư cũ, user ngơ ngác.

GIẢI PHÁP (pattern chuẩn: lazy summarize-on-return, KHÔNG background job):
NestJS phát hiện gap > threshold -> gọi endpoint này 1 lần -> tóm tắt history cũ thành
structured facts (môn/lớp/mục tiêu/ngân sách + gia sư đã xem) + 1 câu recap. NestJS lưu
facts (Postgres) + dùng recap để chào lại "lần trước mình đang tìm... tiếp tục hay tìm mới?"
-> biến "đoán sai âm thầm" thành lựa chọn rõ ràng của user.

Chỉ tốn 1 LLM call cho mỗi user THỰC SỰ quay lại (không tóm tắt phiên user bỏ đi).
"""
from __future__ import annotations

import json

from google import genai
from google.genai import types

from ..core.dependencies import get_gemini_client
from ..models.schemas import (
    SummarizeSessionRequest,
    SummarizeSessionResponse,
    SessionMemory,
)

_MODEL = "gemini-2.5-flash-lite"

_SUMMARIZE_PROMPT = """Bạn tóm tắt một cuộc hội thoại CŨ giữa phụ huynh và trợ lý tìm gia sư Tutora,
để lần sau phụ huynh quay lại thì trợ lý nhớ được ngữ cảnh. Đọc hội thoại, trả về JSON DUY NHẤT:
{{
  "has_pending_search": true nếu phụ huynh ĐANG DỞ việc tìm gia sư (đã nêu môn/lớp/nhu cầu
     nhưng chưa chốt được gia sư nào); false nếu chỉ chào hỏi linh tinh, hỏi vu vơ, không có
     nhu cầu tìm gia sư rõ ràng.
  "recap": "1 câu tiếng Việt ngắn, giọng 'em' gọi 'anh/chị', tóm tắt lần trước phụ huynh đang
     tìm gì — CHỈ điền khi has_pending_search=true, ngược lại để chuỗi rỗng. Vd: 'Lần trước
     mình đang tìm gia sư Ngữ văn lớp 6 cho bé, ưu tiên cô kiên nhẫn, ngân sách dưới 300k'.",
  "memory": {{
     "subject": tên môn học nếu có (vd "Ngữ văn", "Toán") hoặc null,
     "grade": số lớp 1-12 nếu có hoặc null,
     "goal": mục tiêu học nếu có ("mất gốc"/"củng cố"/"nâng cao"/"ôn thi") hoặc null,
     "budget_max": ngân sách trần VND/giờ nếu phụ huynh nêu (vd "dưới 300k" -> 300000) hoặc null,
     "preferences": mong muốn khác về gia sư (tính cách, hình thức học...) dạng câu ngắn, hoặc null,
     "tutors_shown": [danh sách TÊN gia sư đã được gợi ý trong hội thoại, hoặc []]
  }}
}}

QUY TẮC:
- CHỈ trích thông tin CÓ THẬT trong hội thoại. Không suy diễn, không bịa. Không chắc -> null.
- recap phải NGẮN, tự nhiên, đúng những gì phụ huynh đã nói.
- CHỈ trả JSON, không giải thích thêm.

HỘI THOẠI CŨ:
{conversation}

GIA SƯ ĐÃ GỢI Ý (nếu có): {shown}"""


async def summarize_session(body: SummarizeSessionRequest) -> SummarizeSessionResponse:
    gemini: genai.Client = get_gemini_client()

    convo = "\n".join(f'{m.role}: {m.content}' for m in body.history) or "(trống)"
    shown = ", ".join(t.name or t.tutor_id for t in body.shown_tutors) or "(không có)"
    prompt = _SUMMARIZE_PROMPT.format(conversation=convo, shown=shown)

    # History rỗng/quá ngắn -> không có gì để tóm tắt, coi như phiên mới hẳn.
    if len(body.history) < 2:
        return SummarizeSessionResponse(recap="", memory=SessionMemory(), has_pending_search=False)

    try:
        resp = await _generate_json(gemini, prompt)
        data = json.loads(resp)
    except Exception as e:
        # Lỗi tóm tắt -> trả rỗng, NestJS fallback về chào mới (an toàn, không chặn user).
        print(f"summarize_session error: {e}")
        return SummarizeSessionResponse(recap="", memory=SessionMemory(), has_pending_search=False)

    mem = data.get("memory") or {}
    return SummarizeSessionResponse(
        recap=(data.get("recap") or "").strip(),
        has_pending_search=bool(data.get("has_pending_search")),
        memory=SessionMemory(
            subject=mem.get("subject"),
            grade=mem.get("grade"),
            goal=mem.get("goal"),
            budget_max=mem.get("budget_max"),
            preferences=mem.get("preferences"),
            tutors_shown=mem.get("tutors_shown") or [],
        ),
    )


async def _generate_json(gemini: genai.Client, prompt: str) -> str:
    import asyncio
    resp = await asyncio.to_thread(
        gemini.models.generate_content,
        model=_MODEL,
        contents=prompt,
        config=types.GenerateContentConfig(
            temperature=0.2,
            response_mime_type="application/json",
            thinking_config=types.ThinkingConfig(thinking_budget=0),
        ),
    )
    return resp.text
