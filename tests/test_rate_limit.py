"""Verify the rate-limit wiring used by the live API.

Builds a minimal FastAPI app with the same ``_key_func`` + ``Limiter`` +
exception-handler wiring that ``backend.server.server.create_app`` uses,
and exercises every contract the live ``/chat`` route depends on:
- per-IP cap kicks in (200 -> 429 transition)
- ``rate_limit.enabled=False`` disables throttling
- 429 body is slowapi's JSON detail
- ``TRUST_PROXY=true`` partitions buckets by ``X-Forwarded-For``
- ``TRUST_PROXY=false`` ignores ``X-Forwarded-For``
"""
from __future__ import annotations

from fastapi import FastAPI, Request
from fastapi.testclient import TestClient
from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded

from backend.server.server import _key_func


def _wire_limiter_app(rpm: int, *, enabled: bool = True, trust_proxy: bool = False) -> FastAPI:
    """Build the same Limiter + handler + key_func wiring create_app() uses."""
    limiter = Limiter(key_func=_key_func(trust_proxy=trust_proxy), enabled=enabled)
    app = FastAPI()
    app.state.limiter = limiter
    app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)

    @app.post("/chat")
    @limiter.limit(f"{rpm}/minute")
    async def chat(request: Request):
        return {"ok": True}

    return app


def test_rate_limit_blocks_after_cap():
    client = TestClient(_wire_limiter_app(rpm=3))
    statuses = [client.post("/chat", json={}).status_code for _ in range(5)]
    assert statuses[:3] == [200, 200, 200]
    assert statuses[3:] == [429, 429]


def test_disabled_limiter_allows_all():
    client = TestClient(_wire_limiter_app(rpm=2, enabled=False))
    assert all(client.post("/chat", json={}).status_code == 200 for _ in range(8))


def test_429_response_body_matches_slowapi_default():
    client = TestClient(_wire_limiter_app(rpm=1))
    client.post("/chat", json={})
    resp = client.post("/chat", json={})
    assert resp.status_code == 429
    body = resp.json()
    msg = body.get("error") or body.get("detail") or ""
    assert "Rate limit exceeded" in msg


def test_trust_proxy_partitions_buckets_by_xff():
    client = TestClient(_wire_limiter_app(rpm=1, trust_proxy=True))
    a1 = client.post("/chat", json={}, headers={"x-forwarded-for": "10.0.0.1"})
    a2 = client.post("/chat", json={}, headers={"x-forwarded-for": "10.0.0.1"})
    b1 = client.post("/chat", json={}, headers={"x-forwarded-for": "10.0.0.2"})
    assert a1.status_code == 200
    assert a2.status_code == 429
    assert b1.status_code == 200


def test_trust_proxy_false_ignores_xff():
    client = TestClient(_wire_limiter_app(rpm=1, trust_proxy=False))
    a1 = client.post("/chat", json={}, headers={"x-forwarded-for": "10.0.0.1"})
    a2 = client.post("/chat", json={}, headers={"x-forwarded-for": "10.0.0.2"})
    assert a1.status_code == 200
    assert a2.status_code == 429
