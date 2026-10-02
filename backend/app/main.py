from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.v1.router import api_router
from app.core.config import get_settings
from app.core.errors import register_error_handlers
from app.core.logging import configure_logging
from app.core.middleware import CSRF_HEADER, register_middleware


def create_app() -> FastAPI:
    settings = get_settings()
    configure_logging(settings.log_level)

    app = FastAPI(
        title="Districom · Inteligencia Comercial",
        version="0.1.0",
        openapi_url="/api/v1/openapi.json",
        docs_url="/api/docs",
        redoc_url=None,
    )
    register_error_handlers(app)
    register_middleware(app)
    if settings.cors_origins:
        # Production runs same-origin behind the Next.js rewrite; CORS is for local dev only.
        app.add_middleware(
            CORSMiddleware,
            allow_origins=settings.cors_origins,
            allow_credentials=True,
            allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE"],
            allow_headers=["Content-Type", CSRF_HEADER, "X-Request-ID"],
        )
    app.include_router(api_router)
    return app


app = create_app()
