from fastapi import FastAPI, HTTPException, Request
from pydantic import BaseModel
from app.rag_chain import get_rag_chain
import app.rag_chain
from app.memory_manager import get_memory
from langchain_core.runnables.history import RunnableWithMessageHistory
from langsmith import traceable

from fastapi.middleware.cors import CORSMiddleware
from fastapi import Request, Response
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, HTMLResponse

import logging

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)

class ChatRequest(BaseModel):
    message : str
    session_id : str

api = FastAPI()

api.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # or restrict to specific domains
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Custom middleware to set X-Frame-Options header
@api.middleware("http")
async def allow_iframe_localhost(request: Request, call_next):
    response: Response = await call_next(request)

    # Only allow iframe embedding from localhost during local dev
    if request.client.host in ["127.0.0.1", "localhost"]:
        response.headers["X-Frame-Options"] = "ALLOWALL"

    return response

api.mount("/static", StaticFiles(directory="static"), name="static")

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

base_chain, llm = get_rag_chain()
memory_chain = RunnableWithMessageHistory(
    runnable=base_chain,
    get_session_history=lambda session_id: get_memory(session_id=session_id, llm=llm),
    input_messages_key="input",
    history_messages_key="chat_history",
    output_messages_key="answer"
)


@traceable(name="RAG Support Chat")
@api.post("/chat")
async def chat(req: ChatRequest):

    logger.info(f"Received message: {req.message} from session: {req.session_id}")

    try:
        result = memory_chain.invoke(
            {"input" : req.message},
            config = {
                "configurable": { "session_id" : req.session_id }
            }
        )

        logger.info(f"Response: {result['answer']}")
        return {"response": result["answer"]}
    
    except Exception as e:
        logger.exception("Error processing chain")
        return {"error": "Internal service error"}
    
