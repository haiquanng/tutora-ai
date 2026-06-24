from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from slowapi.errors import RateLimitExceeded
from .routers import health, solve, recommend, tutor_chat, agent
from .core.middleware import configure_middleware
from .core.openapi import configure_openapi
from .core.limiter import limiter

app = FastAPI(
    title="Tutora AI",
    description="Tutor AI API",
    version="1.0.0",
)

app.state.limiter = limiter

@app.exception_handler(RateLimitExceeded)
async def rate_limit_handler(_: Request, exc: RateLimitExceeded):
    return JSONResponse(
        status_code=429,
        content={
            "error": "too_many_requests",
            "message": "Số lượt yêu cầu đã đạt giới hạn. Vui lòng quay lại sau.",
            "retry_after": str(exc.retry_after) if hasattr(exc, "retry_after") else "60",
        },
    )

configure_middleware(app)
configure_openapi(app)

app.include_router(health.router, tags=["health"])
app.include_router(solve.router, tags=["solve"])
app.include_router(recommend.router, tags=["recommend"])
app.include_router(tutor_chat.router, tags=["tutor-chat"])
app.include_router(agent.router, tags=["agent"])
