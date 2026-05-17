import base64
import secrets
from fastapi import FastAPI, Request, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import Response
from .routers import solve, solve_v2, health
from .core.config import get_settings

_DOCS_PATHS = {"/docs", "/redoc", "/openapi.json"}

app = FastAPI(
    title="Tutora AI",
    description="Vietnamese Math Tutor API",
    version="1.0.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

@app.middleware("http")
async def auth_middleware(request: Request, call_next):
    settings = get_settings()
    path = request.url.path

    # Docs: Basic Auth
    if path in _DOCS_PATHS:
        auth = request.headers.get("Authorization", "")
        if auth.startswith("Basic "):
            try:
                decoded = base64.b64decode(auth[6:]).decode()
                username, password = decoded.split(":", 1)
                if (
                    secrets.compare_digest(username, settings.docs_username)
                    and secrets.compare_digest(password, settings.docs_password)
                ):
                    return await call_next(request)
            except Exception:
                pass
        return Response(
            status_code=401,
            headers={"WWW-Authenticate": 'Basic realm="Tutora Docs"'},
        )

    # Health: public
    if path == "/health":
        return await call_next(request)

    # API endpoints: X-API-Key
    key = request.headers.get("X-API-Key")
    if not key or not secrets.compare_digest(key, settings.api_key):
        raise HTTPException(status_code=403, detail="Invalid or missing API key")

    return await call_next(request)

app.include_router(health.router, tags=["health"])
app.include_router(solve.router, tags=["solve"])
app.include_router(solve_v2.router, tags=["solve-v2"])
