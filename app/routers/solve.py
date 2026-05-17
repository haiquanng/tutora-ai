from fastapi import APIRouter, Depends, HTTPException, Request
from typing import Optional
from ..models.schemas import SolveRequest, SolveResponse, Step
from ..services import ocr, classifier, rag, solver
from ..core.dependencies import get_embed_model, get_supabase, get_gemini_client
from ..core.config import get_settings
from ..core.limiter import limiter, RATE_LIMIT_PER_MINUTE, RATE_LIMIT_PER_HOUR

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

    return await _build_response(
        problem_text, body.grade, body.chapter,
        settings, gemini, sb, embed_model
    )
