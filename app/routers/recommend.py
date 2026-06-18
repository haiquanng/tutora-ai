from fastapi import APIRouter
from ..models.schemas import TutorRecommendRequest, TutorRecommendResponse
from ..services.tutor_matching import match_tutors

router = APIRouter(prefix="/api/v1")


@router.post("/tutors/recommend", response_model=TutorRecommendResponse)
async def recommend_tutors(body: TutorRecommendRequest):
    results = await match_tutors(
        query=body.query or None,
        candidate_ids=body.candidate_ids or None,
        top_k=body.top_k,
    )
    return TutorRecommendResponse(results=results, total=len(results))
