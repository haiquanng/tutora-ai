from fastapi import APIRouter
from ..models.schemas import (
    AgentRequest, AgentResponse,
    SummarizeSessionRequest, SummarizeSessionResponse,
)
from ..services.agent import run_agent
from ..services.session_memory import summarize_session

router = APIRouter(prefix="/api/v1")


@router.post("/agent", response_model=AgentResponse)
async def agent_endpoint(body: AgentRequest):
    return await run_agent(body)


@router.post("/summarize-session", response_model=SummarizeSessionResponse)
async def summarize_session_endpoint(body: SummarizeSessionRequest):
    # NestJS gọi khi phát hiện user quay lại sau gap dài -> tóm tắt phiên cũ để welcome-back.
    return await summarize_session(body)
