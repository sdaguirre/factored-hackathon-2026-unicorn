"""Punto de entrada FastAPI. `create_app()` permite construir la app con otra configuracion (tests, despliegues)."""
from __future__ import annotations

import logging
import re
import secrets
import time
import uuid

import yaml

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware

from app import __version__
from app.agent.llm import make_llm
from app.agent.orchestrator import DEFAULT_REWRITE_KINDS, Orchestrator
from app.agent.tools import HandoffQueue
from app.api import chat, sessions
from app.config import Settings, get_settings
from app.core.outbox import Outbox
from app.core.ratelimit import AuthLockout
from app.core.sessions import SessionStore
from app.data.repository import CustomerRepository, SnapshotRepository
from app.deps import AppState
from app.errors import register_error_handlers
from app.logging_setup import log, setup_logging, trace_id_var
from app.core.offers import OfferStore
from app.policy import engine as eng

logger = logging.getLogger("chat.http")
_TRACE_OK = re.compile(r"^[A-Za-z0-9\-]{8,64}$")


def _rewrite_kinds(settings: Settings) -> frozenset[str]:
    raw = settings.llm_rewrite_kinds.strip()
    if not raw:
        return DEFAULT_REWRITE_KINDS
    return frozenset() if raw == "none" else frozenset(k.strip() for k in raw.split(",") if k.strip())


def check_policy_version(repo: CustomerRepository, policy: eng.Policy, env: str) -> None:
    """El perfil (export de gold) y la politica (data/reference) deben ser la misma version: un perfil 0.3 con la politica
    0.4 ofreceria plazos que gold ya no permite (sin tope por edad). En prod no arranca; en dev solo avisa."""
    found = repo.policy_versions()
    if found == {policy.version}:
        return
    msg = (f"El perfil de credito es de la politica {sorted(found)} y la politica cargada es {policy.version}: "
           "descargue de nuevo el export de gold (data/scripts/export_gold.py) y regenere el snapshot.")
    if env == "dev":
        logging.getLogger("chat").warning(msg)
    else:
        raise RuntimeError(msg)


def build_state(settings: Settings) -> AppState:
    secret = settings.jwt_secret
    if not secret:
        secret = secrets.token_urlsafe(32)
        logging.getLogger("chat").warning("CHAT_JWT_SECRET vacio: se genero uno temporal; las sesiones no sobreviven al reinicio.")
    sink = None
    if settings.repository == "databricks":
        from app.data.databricks_repository import DatabricksOfferSink, DatabricksRepository, SqlWarehouse

        sql = SqlWarehouse(settings.databricks_warehouse_id, settings.databricks_profile, settings.databricks_timeout_seconds)
        repo = DatabricksRepository(sql, settings.databricks_silver_schema, settings.databricks_gold_schema,
                                    ttl_s=settings.databricks_cache_seconds)
        if settings.databricks_write_offers:
            sink = DatabricksOfferSink(sql, settings.databricks_gold_schema)
    else:
        repo = SnapshotRepository(settings.data_dir)
    policy = eng.load_policy()
    check_policy_version(repo, policy, settings.env)
    with open(settings.rules_path, encoding="utf-8") as f:
        rules = yaml.safe_load(f)
    queue = HandoffQueue()
    outbox = Outbox(settings.outbox_dir)
    offers = OfferStore(settings.offers_path, sink)
    return AppState(
        settings=settings, repo=repo, policy=policy, rules=rules, jwt_secret=secret, queue=queue, outbox=outbox, offers=offers,
        store=SessionStore(settings.session_ttl_minutes),
        lockout=AuthLockout(settings.auth_max_attempts, settings.auth_lockout_minutes, secret),
        orchestrator=Orchestrator(repo, policy, rules, make_llm(settings), queue, _rewrite_kinds(settings), outbox, offers),
    )


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or get_settings()
    setup_logging()
    app = FastAPI(
        title="Chat de credito - backend",
        version=__version__,
        description="Agente conversacional con verificacion por preguntas de seguridad, politica de credito "
                    "determinista y derivacion a humano. Datos y politica sinteticos (prototipo).",
    )
    app.state.ctx = build_state(settings)
    register_error_handlers(app)

    if settings.cors_origin_list:
        app.add_middleware(CORSMiddleware, allow_origins=settings.cors_origin_list, allow_methods=["GET", "POST", "DELETE"],
                           allow_headers=["Authorization", "Content-Type", "X-API-Key", "X-Trace-Id"],
                           expose_headers=["X-Trace-Id"])

    @app.middleware("http")
    async def trace_middleware(request: Request, call_next):
        incoming = request.headers.get("x-trace-id", "")
        tid = incoming if _TRACE_OK.match(incoming) else uuid.uuid4().hex[:16]
        token = trace_id_var.set(tid)
        t0 = time.perf_counter()
        try:
            response = await call_next(request)
            response.headers["X-Trace-Id"] = tid
            # Se registra la ruta sin query ni cuerpo: nunca documentos ni respuestas de seguridad.
            log(logger, "request", method=request.method, path=request.url.path, status=response.status_code,
                latency_ms=int((time.perf_counter() - t0) * 1000))
            return response
        finally:
            trace_id_var.reset(token)

    @app.get("/health", tags=["ops"])
    def health() -> dict:
        return {"status": "ok"}

    @app.get("/v1/meta", tags=["ops"])
    def meta(request: Request) -> dict:
        st: AppState = request.app.state.ctx
        return {"version": __version__, "llm_provider": st.orchestrator.llm.name,
                "policy_version": st.policy.version, "policy_synthetic": True,
                "data_source": st.settings.data_source,
                "languages": ["es", "pt"]}

    app.include_router(sessions.router)
    app.include_router(chat.router)
    return app


app = create_app()
