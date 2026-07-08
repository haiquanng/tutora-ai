import asyncio
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from slowapi.errors import RateLimitExceeded
from .routers import health, solve, recommend, tutor_chat, agent, embed, extract
from .core.middleware import configure_middleware
from .core.openapi import configure_openapi
from .core.limiter import limiter
from .services.tutor_vector_sync import fast_path_loop, reconcile_sweep_loop


@asynccontextmanager
async def lifespan(_: FastAPI):
    # Đồng bộ metadata tutor_vectors (derived index) từ DB nghiệp vụ — hybrid:
    #  - fast-path: poll incremental mỗi 2 phút (độ trễ thấp, chi phí ~0)
    #  - sweep: full reconcile mỗi 6h (bắt update sót + xoá orphan; sàn đúng đắn)
    # Không sync thì Ranking Core chấm điểm trên rating/giá cũ dần.
    tasks = [asyncio.create_task(fast_path_loop()),
             asyncio.create_task(reconcile_sweep_loop())]
    yield
    for t in tasks:
        t.cancel()


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
app.include_router(tutor_chat.router, tags=["tutor-chat"])
app.include_router(agent.router, tags=["agent"])
app.include_router(embed.router, tags=["embed"])
app.include_router(extract.router, tags=["extract"])
