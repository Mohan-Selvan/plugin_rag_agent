from fastapi import FastAPI, Request
from pydantic import BaseModel
from app.rag_chain import get_rag_chain
import app.rag_chain
from app.memory_manager import get_memory
from langchain_core.runnables.history import RunnableWithMessageHistory

api = FastAPI()
base_chain = get_rag_chain()

class ChatRequest(BaseModel):
    message : str
    session_id : str

@api.post("/chat")
async def chat(req: ChatRequest):
    
    # memory_chain = RunnableWithMessageHistory(
    #     runnable=base_chain,
    #     get_session_history=lambda session_id: get_memory(session_id),
    #     input_messages_key="input",
    #     history_messages_key="chat_history"
    # )

    
    print("\n--- MEMORY DEBUG ---")

    if req.session_id in app.rag_chain._memory_store:
        for msg in app.rag_chain._memory_store[req.session_id].chat_memory.messages:
            print(f"{msg.type.upper()}: {msg.content}")
    print("--------------------\n")

    result = base_chain.invoke(
        {"input" : req.message},
        config = {
            "configurable": { "session_id" : req.session_id }
        }
    )

    return {"response": result["answer"]}