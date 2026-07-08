"""
Embed service — biến CHỮ thành VECTOR cho question bank.

tutora-ai đóng vai "máy embed" stateless cho .NET: nhận content câu hỏi, trả
vector(768) qua gemini-embedding-2 (cùng model/dim với rag_chunks & tutor_vectors,
để mọi vector trong hệ dùng chung không gian embedding). KHÔNG chạm DB — .NET tự
ghi vector vào cột embedding của bảng questions (DB-A do .NET sở hữu).

Lỗi được cô lập theo từng item: 1 câu embed hỏng (rate-limit, text rỗng...) trả
error riêng cho câu đó, các câu còn lại vẫn có vector -> .NET retry đúng row lỗi.
"""
from __future__ import annotations

import asyncio

from google import genai

from ..models.embed import EmbedRequest, EmbedResponse, EmbedResultItem

GEMINI_EMBED_MODEL = "gemini-embedding-2"
EMBED_DIM = 768
# Gemini rate-limit: nghỉ nhẹ giữa các lô để tránh 429 khi .NET đẩy 1 PDF ~20 câu.
_BATCH_SIZE = 10
_BATCH_PAUSE_SECONDS = 3


def _embed_one(gemini: genai.Client, text: str) -> list[float]:
    result = gemini.models.embed_content(
        model=GEMINI_EMBED_MODEL,
        contents=text,
        config={"output_dimensionality": EMBED_DIM},
    )
    return result.embeddings[0].values


async def embed_questions(body: EmbedRequest, gemini: genai.Client) -> EmbedResponse:
    results: list[EmbedResultItem] = []

    for i, item in enumerate(body.items):
        text = (item.text or "").strip()
        if not text:
            results.append(EmbedResultItem(id=item.id, error="text rỗng"))
            continue
        try:
            # embed_content là sync (SDK) -> đẩy sang thread để không chặn event loop.
            vector = await asyncio.to_thread(_embed_one, gemini, text)
            results.append(EmbedResultItem(id=item.id, embedding=vector))
        except Exception as e:
            results.append(EmbedResultItem(id=item.id, error=f"{type(e).__name__}: {e}"))

        if (i + 1) % _BATCH_SIZE == 0 and i + 1 < len(body.items):
            await asyncio.sleep(_BATCH_PAUSE_SECONDS)

    return EmbedResponse(model=GEMINI_EMBED_MODEL, dim=EMBED_DIM, results=results)
