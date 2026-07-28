from fastapi import APIRouter
from ..services.web_agent.schemas import WebChatRequest, WebChatResponse
from ..services.web_agent.agent import web_chat

router = APIRouter(prefix="/api/v1/assistant")


@router.post("/respond", response_model=WebChatResponse)
async def assistant_respond(body: WebChatRequest):
    # Trợ lý AI web (Tutora-FE) — 3 mục đích: gợi ý gia sư / hỏi hệ thống / từ chối lạc đề.
    # Guardrail → router → specialized handler (pattern Kodee/Hostinger message-hub). Stateless.
    return await web_chat(body)
