from langchain.memory import ConversationBufferMemory
from langchain_core.chat_history import BaseChatMessageHistory
from typing import Dict

_memory_store:Dict[str, ConversationBufferMemory] = {}

def get_memory(session_id: str) -> BaseChatMessageHistory:
    if session_id not in _memory_store:
        print("Creating memory store")
        _memory_store[session_id] = ConversationBufferMemory(
            return_messages=True,
            memory_key="chat_history",
            input_key="input",
            output_key="output"  
        )

    print(f"Session_id : {session_id}, Memory : {_memory_store[session_id]}")
    for msg in _memory_store[session_id].chat_memory.messages:
        print(f" - {msg.type.upper()}: {msg.content}")
    return _memory_store[session_id].chat_memory