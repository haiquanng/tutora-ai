from fastapi import APIRouter
from ..models.schemas import TutorChatRequest, TutorChatResponse
from ..services.tutor_chat import tutor_chat

router = APIRouter(prefix="/api/v1")


@router.post("/tutor-chat", response_model=TutorChatResponse)
async def tutor_chat_endpoint(body: TutorChatRequest):
    return await tutor_chat(body)
