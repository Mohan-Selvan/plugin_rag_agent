"""FastAPI app entrypoint - assembles config, agent, store, and routes."""
from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded
from slowapi.util import get_remote_address

from ..config import get_config, get_env
from ..database.db import SessionStore
from ..rag.agent import build_agent
from ..utils import get_logger, setup_logging
from .api import build_router

log = get_logger(__name__)


def _key_func(trust_proxy: bool):
    """Per-IP key. Honours X-Forwarded-For only when TRUST_PROXY=true."""
    def f(request: Request) -> str:
        if trust_proxy:
            xff = request.headers.get("x-forwarded-for")
            if xff: return xff.split(",")[0].strip()
        return get_remote_address(request)
    return f


def create_app() -> FastAPI:
    """Build the FastAPI app with the agent, session store, rate limiter, CORS, and routes."""
    cfg = get_config(); env = get_env()
    setup_logging(cfg.logging, env.log_level)
    log.info("starting plugin-rag api: model=%s collection=%s",
             cfg.agent.model, cfg.vector_store.collection)
    agent = build_agent(cfg, env)
    sessions = SessionStore(cfg.sessions.sqlite_path)
    limiter = Limiter(key_func=_key_func(env.trust_proxy), enabled=cfg.rate_limit.enabled)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        await sessions.initialise()
        purged = await sessions.purge_expired(cfg.sessions.ttl_hours * 3600)
        if purged: log.info("purged %d expired sessions on startup", purged)
        yield

    app = FastAPI(title="Plugin RAG", version="0.4.0",
                  docs_url=None, redoc_url=None, openapi_url=None, lifespan=lifespan)

    app.state.limiter = limiter
    app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)

    app.add_middleware(CORSMiddleware, allow_origins=cfg.api.cors_origins,
        allow_credentials=False, allow_methods=["GET", "POST", "OPTIONS"], allow_headers=["*"])

    app.include_router(build_router(agent, sessions, cfg, env, limiter))
    return app


app = create_app()
