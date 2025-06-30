from fastapi import FastAPI, Request
from pydantic import BaseModel
from app.rag_chain import get_rag_chain
import app.rag_chain
from app.memory_manager import get_memory
from langchain_core.runnables.history import RunnableWithMessageHistory

class ChatRequest(BaseModel):
    message : str
    session_id : str

api = FastAPI()
base_chain = get_rag_chain()

memory_chain = RunnableWithMessageHistory(
    runnable=base_chain,
    get_session_history=get_memory,
    input_messages_key="input",
    history_messages_key="chat_history",
    output_messages_key="answer"
)

@api.post("/chat")
async def chat(req: ChatRequest):

    result = memory_chain.invoke(
        {"input" : req.message},
        config = {
            "configurable": { "session_id" : req.session_id }
        }
    )

    return {"response": result["answer"]}