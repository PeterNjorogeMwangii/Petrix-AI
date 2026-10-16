from contextlib import asynccontextmanager
from typing import Optional

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import text
from starlette.middleware.sessions import SessionMiddleware
from starlette.responses import JSONResponse

from app.core.config import Settings
from app.database import create_database
from app.mailer import SMTPMailer
from app.oauth import create_oauth
from app.routes import agents, auth, workflows, workspaces
from app.llm_tester import LLMTester
from app.workflows.matching.router import IntentMatcher


def create_app(settings: Optional[Settings] = None, mailer=None) -> FastAPI:
    settings = settings or Settings()
    engine, session_factory = create_database(settings.database_url.get_secret_value())

    @asynccontextmanager
    async def lifespan(application: FastAPI):
        yield
        engine.dispose()

    application = FastAPI(title="Petrix AI", version="0.4.1", description="AI agents, workflows, Jump redirects, session variables, and layered intent matching.", lifespan=lifespan)
    application.state.settings = settings
    application.state.engine = engine
    application.state.session_factory = session_factory
    application.state.mailer = mailer or SMTPMailer(settings)
    application.state.oauth = create_oauth(settings)
    application.state.llm_tester = LLMTester(settings)
    application.state.intent_matcher = IntentMatcher(settings)
    application.add_middleware(CORSMiddleware, allow_origins=[settings.frontend_origin], allow_credentials=True, allow_methods=["GET", "POST"], allow_headers=["Content-Type", "X-Petrix-CSRF"])
    application.add_middleware(SessionMiddleware, secret_key=settings.session_signing_secret.get_secret_value(), session_cookie="petrix_oauth", max_age=600, same_site="lax", https_only=settings.secure_cookies)

    @application.middleware("http")
    async def headers_and_origin(request: Request, call_next):
        if request.method not in {"GET", "HEAD", "OPTIONS"}:
            origin = request.headers.get("origin")
            if origin and origin not in {settings.frontend_origin, settings.api_base_url}:
                return JSONResponse({"detail": "This request origin is not allowed."}, status_code=403)
            if request.headers.get("sec-fetch-site") == "cross-site":
                return JSONResponse({"detail": "Cross-site requests are not allowed."}, status_code=403)
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["X-Frame-Options"] = "DENY"
        if request.url.path.startswith("/api/") and not request.url.path.endswith("/avatar"):
            response.headers["Cache-Control"] = "no-store"
        return response

    @application.get("/api/health", tags=["Health"])
    def health():
        try:
            with engine.connect() as db:
                db.execute(text("SELECT 1"))
        except Exception:
            return JSONResponse({"status": "unavailable"}, status_code=503)
        return {"status": "ok", "service": "Petrix AI"}

    application.include_router(auth.router)
    application.include_router(workspaces.router)
    application.include_router(agents.router)
    application.include_router(workflows.router)
    return application
