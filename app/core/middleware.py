import base64
import secrets
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import Response
from .config import get_settings

_DOCS_PATHS = {"/docs", "/redoc", "/openapi.json"}


def configure_middleware(app: FastAPI) -> None:
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

        if path == "/health":
            return await call_next(request)

        key = request.headers.get("X-API-Key")
        if not key or not secrets.compare_digest(key, settings.api_key):
            return Response(
                status_code=403,
                content='{"detail":"Invalid or missing API key"}',
                media_type="application/json",
            )

        return await call_next(request)
