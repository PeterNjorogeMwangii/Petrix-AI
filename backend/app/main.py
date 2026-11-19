from contextlib import asynccontextmanager
from typing import Optional

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.exception_handlers import request_validation_exception_handler
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import text
from starlette.middleware.sessions import SessionMiddleware
from starlette.responses import JSONResponse

from app.core.config import Settings
from app.database import create_database
from app.mailer import SMTPMailer
from app.oauth import create_oauth
from app.routes import agents, auth, custom_fields, database_connections, knowledge, workflow_assets, workflows, workspaces, teams, inbox, external_integrations, ai_network, tickets
from app.ai_network.client import PeerClient
from app.external_handoff.client import ProviderClient
from app.database_connections.client import DatabaseClient
from app.llm_tester import LLMTester
from app.workflows.matching.router import IntentMatcher
from app.workflows.actions.send_api.client import SendAPIClient
from app.workflows.actions.llm_processing.client import GroqProcessingClient


def create_app(settings: Optional[Settings] = None, mailer=None) -> FastAPI:
    settings = settings or Settings()
    engine, session_factory = create_database(settings.database_url.get_secret_value())

    @asynccontextmanager
    async def lifespan(application: FastAPI):
        worker=None
        advisor_worker=None
        webchat_worker=None
        whatsapp_worker=None
        perception_worker=None
        knowledge_worker=None
        if settings.support_worker_enabled and settings.app_env!='test':
            from app.teamspace.worker import start
            worker=start(application)
        if settings.knowledge_worker_enabled and settings.app_env!='test':
            from app.agent_knowledge.worker import start as start_knowledge
            knowledge_worker=start_knowledge(application)
        if settings.perception_worker_enabled and settings.app_env!='test':
            from app.perception.worker import start as start_perception
            perception_worker=start_perception(application)
        if settings.advisor_worker_enabled and settings.app_env!='test':
            from app.advisor.worker import start as start_advisor
            advisor_worker=start_advisor(application)
        if settings.webchat_worker_enabled and settings.app_env!='test':
            from app.webchat.worker import start as start_webchat
            webchat_worker=start_webchat(application)
        if settings.whatsapp_worker_enabled and settings.app_env!='test':
            from app.whatsapp.worker import start as start_whatsapp
            whatsapp_worker=start_whatsapp(application)
        yield
        if whatsapp_worker:
            whatsapp_worker[0].set()
            import asyncio
            await asyncio.to_thread(whatsapp_worker[1].join,40)
        if webchat_worker:
            webchat_worker[0].set()
            import asyncio
            await asyncio.to_thread(webchat_worker[1].join,40)
        if advisor_worker:
            advisor_worker[0].set()
            import asyncio
            await asyncio.to_thread(advisor_worker[1].join,40)
        if perception_worker:
            perception_worker[0].set()
            import asyncio
            await asyncio.to_thread(perception_worker[1].join,50)
        if knowledge_worker:
            knowledge_worker[0].set()
            import asyncio
            await asyncio.to_thread(knowledge_worker[1].join,50)
        if worker:
            worker[0].set()
            import asyncio
            await asyncio.to_thread(worker[1].join,25)
        engine.dispose()

    from app.analytics.tracking import install
    install()
    application = FastAPI(title="Petrix AI", version="0.35.1", description="WhatsApp publishing with verified Meta credentials, signed webhooks, native workflows/Knowledge, human Inbox handoff, media, 24-hour reply windows and durable delivery activity. Published Webchat channels, isolated visitor sessions, frozen workflows, website embeds, live design previews, pre-chat forms, proactive messages and browser voice/audio. Private documentation-grounded Advisor with durable conversation history, supported attachments, source links, bundled documentation/API reference and fixed Analytics report scrolling. Perception public mention reporting, keywords, Facebook/X collection adapter, durable schedules, logs, translation and honest early-release previews. Workspace Analytics with Live report, Messaging, Chats, CSAT, Tickets, measured agent activity and heatmaps, shared filters, workspace timezone and native chart exports. Unified livechat/ticket Inbox with scoped saved views, personal pins/sort, multi-panel drafts, native attachments, atomic takeover/send, private Copilot and dedicated Knowledge training. Native People CRM with registration, three detail sections, private notes and colours, permission-scoped history and channel/country/date filters, custom-field exports and safe bulk deletion. Workspace Tickets repository with manual creation from Tickets and Inbox, shared four-tab details, CC replies, private notes, scoped filters and exports, retained interaction accounting and signed incoming mail/delivery events. Workspace Livechats repository, scoped filters, five-tab details, private notes, safe transfers, durable export and deletion with retained interaction accounting. Agent Knowledge file/URL/snippet ingestion, durable jobs, bounded retrieval, master prompt guidance and input/output moderation integration. My profile Account and User Aliases, immutable chat/avatar/signature snapshots, manual ticket replies, personal presence/themes/notifications, guarded workspace switching and tabbed Inbox drafts. Teamspace support settings, typed People profiles, ban enforcement, five assignment methods with frozen priorities, durable analysis/co-pilot/export jobs, tags enforcement, overdue tickets, profile and analytics embeds. Workspace General, independent Workspace/Team roles, protected Admin, users/invitations/idle logout, enforced IPv4/IPv6 allowlists, applied logos/colours, actual weighted usage and configurable Stripe billing. Create Ticket with assignment, copied/overridden custom fields, tags, DNS-verified email domains, HTML templates and durable initial notifications. Authenticated AI Agent Handoff with Network credentials, filtered transferred context, durable same-window receiving workflows and file delivery. Permanent External Handoff for verified native Zendesk/Bitrix24 integrations, read-only external history, and durable transfer review. Live Agent Handoff with authenticated Inbox, team members, expiring availability and saved AI return paths, Send Graph with seven interactive chart types and CSV/PNG exports, Auto Team Match and workspace Teams settings, Auto Fill Field and conversation Custom Fields settings, LLM Processing with Groq and agent-owned Knowledge text sources, AI agents, workflows, Close Chat actions and permanent Chat Closed automation and localised rating/survey feedback with saved variables and continuation, localised file attachments and interactive carousels, surveys, conversation language switching, read-only SQL queries, verified database connections, HTTPS API requests, conditional Branch paths, Jump redirects, session variables, and layered intent matching.", lifespan=lifespan)
    from app.workspace_settings.billing import StripeClient
    application.state.stripe_client=StripeClient(settings)
    from app.teamspace.ai import SupportAI
    application.state.support_ai=SupportAI(settings)
    application.state.knowledge_fetcher=SendAPIClient(max_response_bytes=512*1024,validate_response=False)
    from app.agent_knowledge.web import WebsiteReader
    from app.agent_knowledge.moderation import Moderator
    application.state.agent_knowledge_reader=WebsiteReader()
    application.state.agent_moderator=Moderator(settings)
    from app.perception.collector import Collector
    application.state.perception_collector=Collector(settings)
    from app.advisor.client import AdvisorAI
    application.state.advisor_ai=AdvisorAI(settings)
    from app.whatsapp.client import GraphClient
    application.state.whatsapp_client=GraphClient(settings)
    application.state.settings = settings
    application.state.engine = engine
    application.state.session_factory = session_factory
    application.state.mailer = mailer or SMTPMailer(settings)
    application.state.oauth = create_oauth(settings)
    application.state.llm_tester = LLMTester(settings)
    application.state.intent_matcher = IntentMatcher(settings)
    application.state.llm_processing_client = GroqProcessingClient(settings)
    application.state.send_api_client = SendAPIClient(settings.send_api_allowed_private_hosts.split(",") if settings.send_api_allowed_private_hosts else ())
    application.state.database_client = DatabaseClient()
    application.state.external_handoff_client = ProviderClient(application.state.send_api_client)
    application.state.ai_network_client=PeerClient(settings)
    from app.tickets.dns import DomainDNS
    application.state.ticket_dns=DomainDNS()
    application.state.ticket_mailer=mailer if mailer and hasattr(mailer,"send_ticket") else SMTPMailer(settings)
    application.add_middleware(CORSMiddleware, allow_origins=[settings.frontend_origin], allow_credentials=True, allow_methods=["GET", "POST"], allow_headers=["Content-Type", "X-Petrix-CSRF"])
    application.add_middleware(SessionMiddleware, secret_key=settings.session_signing_secret.get_secret_value(), session_cookie="petrix_oauth", max_age=600, same_site="lax", https_only=settings.secure_cookies)

    @application.exception_handler(RequestValidationError)
    async def network_validation(request, error):
        if '/whatsapp' in request.url.path:
            return JSONResponse({'detail':'Check the WhatsApp fields and request format.'},status_code=422)
        if '/network/' in request.url.path or request.url.path.startswith('/api/ai-network/'):
            return JSONResponse({'detail':'Check the AI agent connection fields and request format.'},status_code=422)
        return await request_validation_exception_handler(request,error)

    @application.middleware("http")
    async def headers_and_origin(request: Request, call_next):
        if request.method not in {"GET", "HEAD", "OPTIONS"} and not request.url.path.startswith(("/api/webchat/","/api/whatsapp/webhooks/")):
            origin = request.headers.get("origin")
            if origin and origin not in {settings.frontend_origin, settings.api_base_url}:
                return JSONResponse({"detail": "This request origin is not allowed."}, status_code=403)
            if request.headers.get("sec-fetch-site") == "cross-site":
                return JSONResponse({"detail": "Cross-site requests are not allowed."}, status_code=403)
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "no-referrer"
        if not (request.url.path.startswith("/api/webchat/") and request.url.path.endswith("/frame")):response.headers["X-Frame-Options"] = "DENY"
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

    from app.workspace_settings.routes import router as workspace_settings_router
    from app.workspace_settings.billing import router as billing_router
    application.include_router(workspace_settings_router)
    application.include_router(billing_router)
    from app.teamspace.routes import router as teamspace_router
    application.include_router(teamspace_router)
    from app.people.routes import router as people_router
    application.include_router(people_router)
    from app.profile.routes import router as profile_router
    application.include_router(profile_router)
    from app.analytics.routes import router as analytics_router
    application.include_router(analytics_router)
    from app.advisor.routes import router as advisor_router
    application.include_router(advisor_router)
    from app.perception.routes import router as perception_router
    application.include_router(perception_router)
    application.include_router(auth.router)
    from app.webchat.routes import router as webchat_router,public as public_webchat_router,staff as webchat_staff_router
    from app.whatsapp.routes import router as whatsapp_router
    from app.whatsapp.webhooks import router as whatsapp_webhooks
    application.include_router(whatsapp_router)
    application.include_router(whatsapp_webhooks)
    application.include_router(webchat_router)
    application.include_router(webchat_staff_router)
    application.include_router(public_webchat_router)
    application.include_router(workspaces.router)
    application.include_router(agents.router)
    application.include_router(database_connections.router)
    application.include_router(knowledge.router)
    from app.agent_knowledge.routes import router as knowledge_sources_router
    application.include_router(knowledge_sources_router)
    application.include_router(custom_fields.router)
    application.include_router(teams.router)
    from app.livechats.routes import router as livechats_router
    application.include_router(livechats_router)
    application.include_router(inbox.router)
    from app.inbox.workspace_routes import router as inbox_workspace_router
    application.include_router(inbox_workspace_router)
    application.include_router(external_integrations.router)
    application.include_router(ai_network.router)
    from app.routes.ticket_workspace import router as ticket_workspace_router
    application.include_router(ticket_workspace_router)
    from app.tickets.mail_events import router as ticket_mail_router
    application.include_router(ticket_mail_router)
    application.include_router(tickets.router)
    application.include_router(workflows.router)
    application.include_router(workflow_assets.router)
    return application
