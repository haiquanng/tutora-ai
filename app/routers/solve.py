import json
import uuid
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import StreamingResponse
from typing import Optional, AsyncGenerator
from ..models.schemas import SolveRequest
from ..services import ocr, classifier, rag, solver_stream
from ..core.dependencies import get_embed_model, get_supabase, get_gemini_client
from ..core.config import get_settings
from ..core.limiter import limiter, RATE_LIMIT_PER_MINUTE, RATE_LIMIT_PER_HOUR

router = APIRouter(prefix="/api/v1")


_OFF_TOPIC_REPLY = "Mình chỉ có thể giúp bạn với các bài toán Toán lớp 9–12 thôi nhé. Bạn có bài toán nào cần giải không?"


async def _sse_generator(
    problem_text: str,
    grade: Optional[str],
    chapter: Optional[str],
    message_id: str,
    session_id: str,
    history: list[dict],
    settings,
    gemini,
    sb,
    embed_model,
) -> AsyncGenerator[str, None]:
    try:
        clf = await classifier.classify_problem(gemini, problem_text)
        is_math_related = clf.get("is_math_related", True)
        is_problem = clf.get("is_problem", True)
        grade = grade or clf.get("grade")
        chapter = chapter or clf.get("chapter")

        if not is_math_related:
            yield f"data: {json.dumps({'id': message_id, 'session_id': session_id, 'delta': _OFF_TOPIC_REPLY, 'done': False}, ensure_ascii=False)}\n\n"
            yield f"data: {json.dumps({'id': message_id, 'session_id': session_id, 'delta': '', 'done': True}, ensure_ascii=False)}\n\n"
            return

        if is_problem:
            rag_chunks, _ = await rag.retrieve_chunks(
                sb=sb, model=embed_model, query=problem_text,
                grade=grade, chapter=chapter, top_k=settings.rag_top_k,
                gemini=gemini,
            )
        else:
            rag_chunks, _ = [], None

        async for chunk in solver_stream.solve_stream(
            client=gemini,
            question=problem_text,
            message_id=message_id,
            session_id=session_id,
            rag_chunks=rag_chunks,
            history=history,
            is_problem=is_problem,
        ):
            yield f"data: {json.dumps(chunk, ensure_ascii=False)}\n\n"

    except Exception as e:
        yield f"data: {json.dumps({'id': message_id, 'session_id': session_id, 'delta': f'[ERROR] {type(e).__name__}: {e}', 'done': True}, ensure_ascii=False)}\n\n"


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
    if body.image_url:
        problem_text = await ocr.extract_from_url(gemini, body.image_url)
    elif body.image_base64:
        problem_text = await ocr.extract_from_image(gemini, body.image_base64)
    elif body.text:
        problem_text = body.text
    else:
        raise HTTPException(status_code=400, detail="Cần text, image_url hoặc image_base64")

    message_id = body.message_id or str(uuid.uuid4())
    session_id = body.chat_id or str(uuid.uuid4())
    history = [m.model_dump() for m in body.history]

    return StreamingResponse(
        _sse_generator(
            problem_text, body.grade, body.chapter,
            message_id, session_id, history,
            settings, gemini, sb, embed_model,
        ),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )
