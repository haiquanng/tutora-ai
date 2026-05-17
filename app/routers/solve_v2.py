import json
import uuid
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import StreamingResponse
from typing import Optional, AsyncGenerator
from ..models.schemas import SolveRequest
from ..services import ocr, classifier, rag, solver_stream_v2, chat_history
from ..core.dependencies import get_embed_model, get_supabase, get_gemini_client
from ..core.config import get_settings
from ..core.limiter import limiter, RATE_LIMIT_PER_MINUTE, RATE_LIMIT_PER_HOUR

router = APIRouter(prefix="/v2")


async def _sse_generator_v2(
    problem_text: str,
    grade: Optional[str],
    chapter: Optional[str],
    message_id: str,
    session_id: str,
    settings,
    gemini,
    sb,
    embed_model,
) -> AsyncGenerator[str, None]:
    is_problem = True
    if not grade or not chapter:
        clf = await classifier.classify_problem(gemini, problem_text)
        is_problem = clf.get("is_problem", True)
        grade = grade or clf.get("grade")
        chapter = chapter or clf.get("chapter")

    if is_problem:
        rag_chunks = await rag.retrieve_chunks(
            sb=sb, model=embed_model, query=problem_text,
            grade=grade, chapter=chapter, top_k=settings.rag_top_k,
            gemini=gemini,
        )
    else:
        rag_chunks = []

    history = await chat_history.get_session_messages(sb=sb, session_id=session_id)

    await chat_history.save_message(
        sb=sb, session_id=session_id, role="user", content=problem_text,
        grade=grade, chapter=chapter,
    )

    full_response: list[str] = []

    async for chunk in solver_stream_v2.solve_stream_v2(
        client=gemini,
        question=problem_text,
        message_id=message_id,
        session_id=session_id,
        rag_chunks=rag_chunks,
        history=history,
        is_problem=is_problem,
    ):
        if not chunk["done"]:
            full_response.append(chunk["delta"])
        yield f"data: {json.dumps(chunk, ensure_ascii=False)}\n\n"

    await chat_history.save_message(
        sb=sb, session_id=session_id, role="assistant",
        content="".join(full_response),
        grade=grade, chapter=chapter, rag_used=len(rag_chunks) > 0,
    )


@router.post("/solve")
@limiter.limit(RATE_LIMIT_PER_MINUTE)
@limiter.limit(RATE_LIMIT_PER_HOUR)
async def solve_stream_v2_endpoint(
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

    return StreamingResponse(
        _sse_generator_v2(
            problem_text, body.grade, body.chapter,
            message_id, session_id,
            settings, gemini, sb, embed_model,
        ),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )
