import asyncio

from fastapi import APIRouter
from ..models.schemas import TutorRecommendRequest, TutorRecommendResponse
from ..services.tutoring_shared.matching import match_tutors
from ..services.tutoring_shared.tutor_embed import embed_tutor

router = APIRouter(prefix="/api/v1")


@router.post("/tutors/recommend", response_model=TutorRecommendResponse)
async def recommend_tutors(body: TutorRecommendRequest):
    results = await match_tutors(
        query=body.query or None,
        candidate_ids=body.candidate_ids or None,
        top_k=body.top_k,
    )
    return TutorRecommendResponse(results=results, total=len(results))


@router.post("/tutors/{tutor_id}/embed")
async def embed_tutor_endpoint(tutor_id: str):
    """Vector hoá 1 gia sư — .NET gọi (fire-and-forget) khi hồ sơ/giá đổi hoặc được duyệt."""
    return await asyncio.to_thread(embed_tutor, tutor_id)
