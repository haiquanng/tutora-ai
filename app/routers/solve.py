import base64
import json
import uuid
from fastapi import APIRouter, Depends, HTTPException, UploadFile, File, Form
from fastapi.responses import StreamingResponse
from typing import Optional, AsyncGenerator
from ..models.schemas import SolveRequest, SolveResponse, Step
from ..services import ocr, classifier, rag, solver, solver_stream, chat_history
from ..core.dependencies import get_embed_model, get_supabase, get_gemini_client
from ..core.config import get_settings
from ..constants.utils import ALLOWED_IMAGE_TYPES

router = APIRouter()

async def _build_response(
    problem_text: str,
    grade: Optional[str],
    chapter: Optional[str],
    settings,
    gemini,
    sb,
    embed_model,
) -> SolveResponse:
    if not grade or not chapter:
        clf = await classifier.classify_problem(gemini, problem_text)
        grade = grade or clf.get("grade")
        chapter = chapter or clf.get("chapter")

    chunks = await rag.retrieve_chunks(
        sb=sb, model=embed_model, query=problem_text,
        grade=grade, chapter=chapter, top_k=settings.rag_top_k
    )

    solution = await solver.solve(
        client=gemini, question=problem_text, rag_chunks=chunks
    )

    steps = [
        Step(
            step=s.get("step", i + 1),
            title=s.get("title", ""),
            content=s.get("content", ""),
            formula=s.get("formula")
        )
        for i, s in enumerate(solution.get("steps", []))
    ]

    return SolveResponse(
        problem_extracted=problem_text,
        grade=grade,
        chapter=chapter,
        steps=steps,
        final_answer=solution.get("final_answer", ""),
        hint=solution.get("hint", ""),
        common_mistakes=solution.get("common_mistakes", []),
        verified=None,
        confidence=float(solution.get("confidence", 0.9)),
        rag_used=len(chunks) > 0
    )


@router.post("/solve", response_model=SolveResponse)
async def solve_endpoint(
    request: SolveRequest,
    settings=Depends(get_settings),
    gemini=Depends(get_gemini_client),
    sb=Depends(get_supabase),
    embed_model=Depends(get_embed_model)
):
    if request.image_base64:
        problem_text = await ocr.extract_from_image(gemini, request.image_base64)
    elif request.text:
        problem_text = request.text
    else:
        raise HTTPException(status_code=400, detail="Cần text hoặc image_base64")

    return await _build_response(
        problem_text, request.grade, request.chapter,
        settings, gemini, sb, embed_model
    )


@router.post("/solve/image", response_model=SolveResponse)
async def solve_image_endpoint(
    file: UploadFile = File(...),
    grade: Optional[str] = Form(None),
    chapter: Optional[str] = Form(None),
    settings=Depends(get_settings),
    gemini=Depends(get_gemini_client),
    sb=Depends(get_supabase),
    embed_model=Depends(get_embed_model)
):
    if file.content_type not in ALLOWED_IMAGE_TYPES:
        raise HTTPException(status_code=400, detail=f"Chỉ chấp nhận ảnh: {', '.join(ALLOWED_IMAGE_TYPES)}")

    image_bytes = await file.read()
    if len(image_bytes) > 10 * 1024 * 1024:
        raise HTTPException(status_code=400, detail="Ảnh tối đa 10MB")

    image_base64 = base64.b64encode(image_bytes).decode()
    problem_text = await ocr.extract_from_image(gemini, image_base64)

    return await _build_response(
        problem_text, grade, chapter,
        settings, gemini, sb, embed_model
    )


async def _sse_generator(
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
    if not grade or not chapter:
        clf = await classifier.classify_problem(gemini, problem_text)
        grade = grade or clf.get("grade")
        chapter = chapter or clf.get("chapter")

    rag_chunks = await rag.retrieve_chunks(
        sb=sb, model=embed_model, query=problem_text,
        grade=grade, chapter=chapter, top_k=settings.rag_top_k
    )

    await chat_history.save_message(
        sb=sb, session_id=session_id, role="user", content=problem_text,
        grade=grade, chapter=chapter,
    )

    full_response: list[str] = []

    async for chunk in solver_stream.solve_stream(
        client=gemini,
        question=problem_text,
        message_id=message_id,
        chat_id=session_id,
        rag_chunks=rag_chunks,
    ):
        if not chunk["done"]:
            full_response.append(chunk["delta"])
        yield f"data: {json.dumps(chunk, ensure_ascii=False)}\n\n"

    await chat_history.save_message(
        sb=sb, session_id=session_id, role="assistant",
        content="".join(full_response),
        grade=grade, chapter=chapter, rag_used=len(rag_chunks) > 0,
    )


@router.post("/solve/stream")
async def solve_stream_endpoint(
    request: SolveRequest,
    settings=Depends(get_settings),
    gemini=Depends(get_gemini_client),
    sb=Depends(get_supabase),
    embed_model=Depends(get_embed_model)
):
    if request.image_base64:
        problem_text = await ocr.extract_from_image(gemini, request.image_base64)
    elif request.text:
        problem_text = request.text
    else:
        raise HTTPException(status_code=400, detail="Cần text hoặc image_base64")

    message_id = request.message_id or str(uuid.uuid4())
    session_id = request.chat_id or str(uuid.uuid4())

    return StreamingResponse(
        _sse_generator(
            problem_text, request.grade, request.chapter,
            message_id, session_id,
            settings, gemini, sb, embed_model,
        ),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )


@router.post("/solve/stream/image")
async def solve_stream_image_endpoint(
    file: UploadFile = File(...),
    grade: Optional[str] = Form(None),
    chapter: Optional[str] = Form(None),
    message_id: Optional[str] = Form(None),
    chat_id: Optional[str] = Form(None),
    settings=Depends(get_settings),
    gemini=Depends(get_gemini_client),
    sb=Depends(get_supabase),
    embed_model=Depends(get_embed_model)
):
    if file.content_type not in ALLOWED_IMAGE_TYPES:
        raise HTTPException(status_code=400, detail=f"Chỉ chấp nhận ảnh: {', '.join(ALLOWED_IMAGE_TYPES)}")

    image_bytes = await file.read()
    if len(image_bytes) > 10 * 1024 * 1024:
        raise HTTPException(status_code=400, detail="Ảnh tối đa 10MB")

    image_base64 = base64.b64encode(image_bytes).decode()
    problem_text = await ocr.extract_from_image(gemini, image_base64)

    mid = message_id or str(uuid.uuid4())
    sid = chat_id or str(uuid.uuid4())

    return StreamingResponse(
        _sse_generator(
            problem_text, grade, chapter,
            mid, sid,
            settings, gemini, sb, embed_model,
        ),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )
