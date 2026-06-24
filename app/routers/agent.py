from fastapi import APIRouter
from ..models.schemas import AgentRequest, AgentResponse
from ..services.agent import run_agent

router = APIRouter(prefix="/api/v1")


@router.post("/agent", response_model=AgentResponse)
async def agent_endpoint(body: AgentRequest):
    return await run_agent(body)
