"""
Fixture dùng chung cho test agent hội thoại.

LƯU Ý QUAN TRỌNG — test này gọi Gemini THẬT (không mock):
- Cần GEMINI_API_KEY hợp lệ trong .env và .NET backend (DOTNET_BE_URL) chạy được,
  vì search_tutors/answer_faq gọi qua .NET /recommend, /subjects...
- KHÔNG tất định: output text của LLM đổi mỗi lần chạy dù cùng input (temperature=0.3).
  Vì vậy assertion PHẢI nhắm vào field có cấu trúc (tutors, awaiting_confirmation,
  confirm_type, context_patch...), KHÔNG so sánh chuỗi reply chính xác.
- Chậm hơn unit test thường (mỗi call ~1-3s do gọi Gemini + .NET) -> chạy riêng
  khỏi suite nhanh, vd `pytest tests/agent -m agent`.
"""
import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.core.config import get_settings

client = TestClient(app)
_settings = get_settings()


def call_agent(message: str, history: list[dict] | None = None,
                channel: str = "zalo", context: dict | None = None,
                shown_tutors: list[dict] | None = None) -> dict:
    """Gọi /api/v1/agent, trả JSON response. Raise nếu status != 200."""
    body = {
        "history": history or [],
        "message": message,
        "channel": channel,
        "context": context or {},
        "shown_tutors": shown_tutors or [],
    }
    r = client.post(
        "/api/v1/agent",
        json=body,
        headers={"X-API-Key": _settings.api_key},
    )
    assert r.status_code == 200, f"agent call failed: {r.status_code} {r.text}"
    return r.json()


@pytest.fixture
def agent():
    return call_agent
