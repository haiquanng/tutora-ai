from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from .routers import solve, solve_v2, health

app = FastAPI(
    title="Tutora AI",
    description="Vietnamese Math Tutor API",
    version="1.0.0"
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(health.router, tags=["health"])
app.include_router(solve.router, tags=["solve"])
app.include_router(solve_v2.router, tags=["solve-v2"])
