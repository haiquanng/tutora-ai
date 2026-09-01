from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from slowapi.errors import RateLimitExceeded
from .routers import health, solve, recommend, assistant, agent, embed, extract, knowledge, assessment, classroom
from .core.middleware import configure_middleware
from .core.openapi import configure_openapi
from .core.limiter import limiter
from .services.telemetry import usage as usage_telemetry


@asynccontextmanager
async def lifespan(_: FastAPI):
    yield
    # Đẩy nốt số liệu token còn trong hàng đợi trước khi tắt (usage gửi theo lô).
    await usage_telemetry.flush()


app = FastAPI(
    title="Tutora AI",
    description="Tutor AI API",
    version="1.0.0",
    lifespan=lifespan,
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
app.include_router(assistant.router, tags=["assistant"])
app.include_router(agent.router, tags=["agent"])
app.include_router(embed.router, tags=["embed"])
app.include_router(extract.router, tags=["extract"])
app.include_router(knowledge.router, tags=["knowledge"])
app.include_router(assessment.router, tags=["assessment"])
app.include_router(classroom.router, tags=["classroom"])
