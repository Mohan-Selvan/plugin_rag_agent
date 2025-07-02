import json
from typing import Optional
from fastapi import FastAPI, HTTPException, Request
from pydantic import BaseModel
from app.rag_chain import get_rag_chain
from app.memory_manager import get_memory
from langchain_core.runnables.history import RunnableWithMessageHistory

from fastapi.middleware.cors import CORSMiddleware
from fastapi import Request, Response
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.util import get_ipaddr
from slowapi.errors import RateLimitExceeded

from uuid import uuid4
from datetime import datetime, timedelta, timezone

from langsmith import traceable
import hashlib

import logging

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s",
)

logger = logging.getLogger(__name__)

class ChatRequest(BaseModel):
    message : str
    session_id : Optional[str] = None

api = FastAPI()
api.mount("/static", StaticFiles(directory="static"), name="static")

api.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # or restrict to specific domains
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

def ip_key_func(request):
    return get_ipaddr(request)

def session_key_func(request):
    return getattr(request.state, "session_id", "unknown-session")



limiter = Limiter(key_func=session_key_func)
api.state.limiter = limiter

base_chain, llm = get_rag_chain()
memory_chain = RunnableWithMessageHistory(
    runnable=base_chain,
    get_session_history=lambda session_id: get_memory(session_id=session_id, llm=llm),
    input_messages_key="input",
    history_messages_key="chat_history",
    output_messages_key="answer"
)

sessions = {}

def get_client_ip(request: Request) -> Optional[str]:
    # Only trust X-Forwarded-For if behind a proxy
    xff = request.headers.get("X-Forwarded-For")
    if xff:
        ip = xff.split(",")[0].strip()
        if ip:
            return ip

    if request.client and request.client.host:
        return request.client.host

    return None

def generate_session_id(request: Request) -> str:
    # Prefer client-provided session_id from query or body (middleware or route handles this)
    # Else fallback to IP-based hashed ID (avoid raw IP)
    ip = get_client_ip(request)
    if not ip:
        raise ValueError("Unable to determine client identity. Session ID required.")

    # Use a hash to avoid exposing IP directly
    return hashlib.sha256(ip.encode()).hexdigest()[:16]

# def create_session(request: Request):
#     session_id = str(uuid4())

#     now_utc = datetime.now(timezone.utc)

#     sessions[session_id] = {
#         "ip": request.client.host,
#         "created" : now_utc,
#         "expires" : now_utc + timedelta(hours = 1)
#     }

#     return 

@api.middleware("http")
async def inject_rate_limiter(request: Request, call_next):
    
    request.state.limiter = limiter

    session_id = ""

    # 1. Try from headers
    if "x-session-id" in request.headers:
        session_id = request.headers["x-session-id"]

    # 2. Try from body (for POST with JSON)
    elif request.method in ("POST", "PUT", "PATCH"):
        try:
            body_bytes = await request.body()
            if body_bytes:
                body = json.loads(body_bytes)
                if "session_id" in body:
                    session_id = body["session_id"]
                # Reset the body so downstream can access it
                request._receive = lambda: {"type": "http.request", "body": body_bytes}
        except Exception:
            pass

    # 3. Try from query params (as fallback)
    elif "session_id" in request.query_params:
        session_id = request.query_params["session_id"]

    # Set session_id in state for logging, tracing, etc.
    request.state.session_id = session_id

    response = await call_next(request)
    response.headers["X-Session-ID"] = request.state.session_id
    return response


@api.exception_handler(RateLimitExceeded)
async def rate_limit_handler(request: Request, exc: RateLimitExceeded):
    return JSONResponse(
        status_code=429,
        content={"response": "Rate limit exceeded. Please try again in a few seconds."}
    )

@api.exception_handler(StarletteHTTPException)
async def http_exception_handler(request: Request, exc: StarletteHTTPException):
    return JSONResponse(
        status_code=exc.status_code,
        content={"response": exc.detail or "An error occurred."}
    )

@api.exception_handler(Exception)
async def general_exception_handler(request: Request, exc: Exception):
    return JSONResponse(
        status_code=500,
        content={"response": "An internal server error occurred. Please try again later."}
    )

@api.get("/health")
def health():
    return {"status": "ok"}

@api.get("/chat-ui", response_class=HTMLResponse)
async def serve_chat_ui():
    return FileResponse("static/chat_ui/index.html")

@api.get("/widget", response_class=HTMLResponse)
async def serve_widget(request: Request):
    client_ip = request.client.host
    if client_ip not in ["127.0.0.1", "your-approved-client-ip"]:
        raise HTTPException(status_code=403, detail="Forbidden")

    with open("static/widgets/widget.js") as f:
        return HTMLResponse(content=f.read(), media_type="application/javascript")
    
    
@traceable(name="RAG Support Chat")
@api.post("/chat")
@limiter.limit("3/minute")
async def chat(req: ChatRequest, request: Request):

    session_id = request.state.session_id

    if not session_id:
        return JSONResponse(
            status_code=400,
            content={"response": "Missing session_id. Cannot proceed."}
        )
    
    logger.info(f"[{session_id}] Received message: {req.message}")

    # # Manually check session rate limit
    # await limiter.limit("1/minute", key_func=session_key_func)(request)

    # # Manually check IP rate limit
    # await limiter.limit("1/minute", key_func=ip_key_func)(request)

    try:
        result = memory_chain.invoke(
            {"input" : req.message},
            config={
            "tags": [f"session:{req.session_id}"],
            "metadata": {"session_id": req.session_id},
            "configurable": {"session_id": req.session_id}
            }
        )

        logger.info(f"Response: {result['answer']}")
        return {"response": result["answer"]}
    
    except Exception as e:
        logger.error(f"Exception occured: session : [{req.session_id}] : {e}")
        return {"response": "Sorry, something went wrong. Please try again a few minutes."}
    