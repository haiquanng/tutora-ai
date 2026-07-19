import json
import logging
import uuid
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import StreamingResponse
from typing import AsyncGenerator
from ..models.schemas import SolveRequest
from ..services import ocr, classifier, rag, solver_stream
from ..core.dependencies import get_embed_model, get_supabase, get_gemini_client
from ..core.config import get_settings
from ..core.limiter import limiter, RATE_LIMIT_PER_MINUTE, RATE_LIMIT_PER_HOUR

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1")


_NO_MATH_REPLY = (
    "Mình chưa đọc được đề toán trong ảnh này. Bạn thử chụp lại rõ nét hơn "
    "(đủ sáng, không bị mờ/nghiêng, lấy trọn đề bài) hoặc gõ đề trực tiếp giúp mình nhé!"
)

# Lỗi kỹ thuật (Gemini timeout, DB...) -> xin lỗi thân thiện, không lộ stacktrace.
_ERROR_REPLY = (
    "Xin lỗi bạn, mình đang gặp chút trục trặc khi xử lý bài này. "
    "Bạn thử gửi lại sau giây lát nhé!"
)


def _sse_reply(message_id: str, session_id: str, text: str) -> str:
    """Đóng gói 1 câu trả lời tĩnh thành 2 SSE event (delta + done)."""
    out = f"data: {json.dumps({'id': message_id, 'session_id': session_id, 'delta': text, 'done': False}, ensure_ascii=False)}\n\n"
    out += f"data: {json.dumps({'id': message_id, 'session_id': session_id, 'delta': '', 'done': True}, ensure_ascii=False)}\n\n"
    return out


async def _resolve_problem_text(body: SolveRequest, gemini) -> str:
    """Lấy đề bài từ text/ảnh. Trả NO_MATH nếu ảnh không đọc được đề."""
    if body.image_url:
        return await ocr.extract_from_url(gemini, body.image_url)
    if body.image_base64:
        return await ocr.extract_from_image(gemini, body.image_base64)
    return body.text or ""


async def _sse_generator(
    body: SolveRequest,
    message_id: str,
    session_id: str,
    history: list[dict],
    settings,
    gemini,
    sb,
    embed_model,
) -> AsyncGenerator[str, None]:
    try:
        # OCR nằm trong generator để mọi lỗi ảnh cũng thành SSE (mobile parse đồng nhất).
        problem_text = await _resolve_problem_text(body, gemini)
        if problem_text == ocr.NO_MATH:
            yield _sse_reply(message_id, session_id, _NO_MATH_REPLY)
            return

        grade, chapter = body.grade, body.chapter
        clf = await classifier.classify_problem(gemini, problem_text)
        is_math_related = clf.get("is_math_related", True)
        is_problem = clf.get("is_problem", True)
        grade = grade or clf.get("grade")
        chapter = chapter or clf.get("chapter")

        # Off-topic hoặc không phải bài toán -> để LLM (CHAT_SYSTEM, đã kèm _SCOPE_RULE)
        # tự trả lời/từ chối tự nhiên theo persona, KHÔNG dùng câu cứng hard-code.
        if is_math_related and is_problem:
            # rag_chunks = bài mẫu SGK VN (kèm lời giải) -> AI bám PHƯƠNG PHÁP chuẩn.
            # (Bảng questions bank riêng chưa có ở Supabase này nên không truy vấn.)
            rag_chunks, _ = await rag.retrieve_chunks(
                sb=sb, model=embed_model, query=problem_text,
                grade=grade, chapter=chapter, top_k=settings.rag_top_k,
                gemini=gemini,
            )
            bank_matches = []
        else:
            rag_chunks, bank_matches, is_problem = [], [], False

        async for chunk in solver_stream.solve_stream(
            client=gemini,
            question=problem_text,
            message_id=message_id,
            session_id=session_id,
            rag_chunks=rag_chunks,
            history=history,
            is_problem=is_problem,
            bank_matches=bank_matches,
        ):
            yield f"data: {json.dumps(chunk, ensure_ascii=False)}\n\n"

    except Exception as e:
        # Log chi tiết ở server; client chỉ nhận câu xin lỗi thân thiện (không lộ stacktrace).
        logger.exception("solve_stream failed (session=%s): %s", session_id, e)
        yield _sse_reply(message_id, session_id, _ERROR_REPLY)


@router.post("/solve")
@limiter.limit(RATE_LIMIT_PER_MINUTE)
@limiter.limit(RATE_LIMIT_PER_HOUR)
async def solve_endpoint(
    request: Request,
    body: SolveRequest,
    settings=Depends(get_settings),
    gemini=Depends(get_gemini_client),
    sb=Depends(get_supabase),
    embed_model=Depends(get_embed_model)
):
    if not (body.image_url or body.image_base64 or body.text):
        raise HTTPException(status_code=400, detail="Cần text, image_url hoặc image_base64")

    message_id = body.message_id or str(uuid.uuid4())
    session_id = body.chat_id or str(uuid.uuid4())
    history = [m.model_dump() for m in body.history]

    return StreamingResponse(
        _sse_generator(
            body, message_id, session_id, history,
            settings, gemini, sb, embed_model,
        ),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )
