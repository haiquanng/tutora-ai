from fastapi import APIRouter, Depends

from ..models.embed import EmbedRequest, EmbedResponse
from ..core.dependencies import get_gemini_client
from ..services.embed import embed_questions

router = APIRouter(prefix="/api/v1")


@router.post("/embed", response_model=EmbedResponse)
async def embed_endpoint(body: EmbedRequest, gemini=Depends(get_gemini_client)):
    """Batch embed question bank cho .NET. Nhận [{id, text}], trả [{id, embedding}].
    Stateless — không ghi DB; .NET tự UPDATE cột embedding ở DB-A."""
    return await embed_questions(body, gemini)
