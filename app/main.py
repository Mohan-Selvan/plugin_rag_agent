from fastapi import FastAPI, Request
from pydantic import BaseModel
from app.rag_chain import get_rag_chain
import app.rag_chain
from app.memory_manager import get_memory
from langchain_core.runnables.history import RunnableWithMessageHistory
from langsmith import traceable

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