from fastapi import APIRouter
from ..models.schemas import (
    AgentRequest, AgentResponse,
    SummarizeSessionRequest, SummarizeSessionResponse,
    DirectSearchRequest, DirectSearchResponse,
)
from ..services.agent import run_agent, search_tutors_direct
from ..services.session_memory import summarize_session

router = APIRouter(prefix="/api/v1")


@router.post("/agent", response_model=AgentResponse)
async def agent_endpoint(body: AgentRequest):
    return await run_agent(body)


@router.post("/tutors/search-direct", response_model=DirectSearchResponse)
async def search_tutors_direct_endpoint(body: DirectSearchRequest):
    # Mini App gọi thẳng khi tiêu chí đã rõ (id thật từ form) — KHÔNG qua hội thoại/LLM.
    return await search_tutors_direct(body)


@router.post("/summarize-session", response_model=SummarizeSessionResponse)
async def summarize_session_endpoint(body: SummarizeSessionRequest):
    # NestJS gọi khi phát hiện user quay lại sau gap dài -> tóm tắt phiên cũ để welcome-back.
    return await summarize_session(body)
