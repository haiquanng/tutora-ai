"""
FAQ handler (specialized handler) — trả lời câu hỏi về HỆ THỐNG/CHÍNH SÁCH Tutora bằng RAG.

Kodee: RAG chống hallucination — lấy passage từ vector DB, ép LLM trả lời DỰA HOÀN TOÀN
vào passage, temperature thấp. Rỗng → nói thật "chưa có thông tin", TUYỆT ĐỐI không bịa
(bài học nameserver của Kodee: LLM tự tin bịa dữ liệu factual nếu không có RAG).

Nguồn KB: bảng RIÊNG tutora_kb_chunks
"""
from __future__ import annotations

from google.genai import types

from ..schemas import WebChatResponse
from ..handlers.base import BaseHandler, HandlerContext
from ...knowledge.retrieve import retrieve_kb
from ....core.dependencies import get_gemini_client
from ...telemetry.usage import track

_MODEL = "gemini-2.5-flash-lite"

_NO_INFO = (
    "Dạ phần này mình chưa có thông tin chính xác ạ. Anh/chị liên hệ hỗ trợ Tutora để "
    "được giải đáp cụ thể giúp mình nhé!"
)


class FaqHandler(BaseHandler):
    label = "faq"

    async def handle(self, ctx: HandlerContext) -> WebChatResponse:
        try:
            chunks = await retrieve_kb(ctx.message)
        except Exception as e:
            print(f"web faq handler error: {e}")
            chunks = []

        passages = [p for p in (c.get("content") for c in chunks) if p]
        if not passages:
            return WebChatResponse(reply=_NO_INFO, intent="faq", suggestions=ctx.suggestions)

        ctx_text = "\n".join(f"- {p}" for p in passages)
        reply = _answer_from_passages(ctx.message, ctx_text, ctx.history)
        return WebChatResponse(
            reply=reply or _NO_INFO, intent="faq", suggestions=ctx.suggestions,
        )


def _answer_from_passages(question: str, ctx_text: str, history: list[dict]) -> str:
    """LLM diễn đạt câu trả lời DỰA HOÀN TOÀN vào passage — temp thấp, chống bịa."""
    gemini = get_gemini_client()
    convo = "\n".join(f'{m["role"]}: {m["content"]}' for m in history[-6:])
    prompt = (
        "Bạn là trợ lý Tutora. Trả lời câu hỏi của người dùng DỰA HOÀN TOÀN vào thông tin "
        "tham chiếu bên dưới, KHÔNG bịa thêm bất kỳ chi tiết nào ngoài đó. Nếu thông tin "
        "tham chiếu KHÔNG đủ trả lời, nói thật là chưa có thông tin và mời liên hệ hỗ trợ "
        "Tutora. Giọng thân thiện, ngắn gọn, tiếng Việt, xưng 'mình'.\n"
        "TUYỆT ĐỐI KHÔNG chào hỏi/giới thiệu lại bản thân ('Chào bạn', 'mình là trợ lý "
        "Tutora'...) — đang giữa cuộc trò chuyện, chào lại mỗi lượt rất máy móc.\n\n"
        f"THÔNG TIN THAM CHIẾU:\n{ctx_text}\n\n"
        f"{('Hội thoại gần đây:' + chr(10) + convo + chr(10) + chr(10)) if convo else ''}"
        f"Câu hỏi: {question}"
    )
    try:
        with track("web_agent_faq", _MODEL) as _t:
            resp = _t.done(gemini.models.generate_content(
                model=_MODEL,
                contents=prompt,
                config=types.GenerateContentConfig(
                    temperature=0.2,
                    thinking_config=types.ThinkingConfig(thinking_budget=0),
                ),
            ))
        return (resp.text or "").strip()
    except Exception as e:
        print(f"web faq answer error: {e}")
        return ""
