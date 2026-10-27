"""Application entry point."""

from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse

from app.api.accounts import router as accounts_router
from app.api.auth import router as auth_router
from app.api.chat import router as chat_router
from app.api.mcp import router as mcp_router
from app.api.service_requests import router as service_requests_router
from app.api.transactions import router as transactions_router
from app.core.config import settings
from app.core.observability import RequestObservabilityMiddleware


def create_app(*, docs_enabled: bool | None = None) -> FastAPI:
    """Create the API application with optional interactive documentation."""
    enable_docs = settings.API_DOCS_ENABLED if docs_enabled is None else docs_enabled
    application = FastAPI(
        title="Multi-Agent Banking Platform",
        docs_url="/docs" if enable_docs else None,
        redoc_url=None,
    )
    application.add_middleware(RequestObservabilityMiddleware)
    application.include_router(auth_router)
    application.include_router(chat_router)
    application.include_router(mcp_router)
    application.include_router(accounts_router)
    application.include_router(service_requests_router)
    application.include_router(transactions_router)

    @application.get("/health", tags=["Health"])
    async def health_check():
        """Health check endpoint."""
        return {"status": "ok"}

    @application.get("/", include_in_schema=False)
    async def banking_console() -> FileResponse:
        """Serve the small same-origin demo client without exposing Swagger."""
        return FileResponse(Path(__file__).with_name("web") / "index.html")

    return application


app = create_app()
