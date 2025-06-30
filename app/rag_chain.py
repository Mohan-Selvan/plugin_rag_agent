import os
from dotenv import load_dotenv
from app.embeddings import get_gemini_embeddings
from qdrant_client import QdrantClient

from langchain_community.vectorstores import Qdrant
from langchain_google_genai import ChatGoogleGenerativeAI

from langchain_core.prompts import ChatPromptTemplate, MessagesPlaceholder
from langchain.chains.history_aware_retriever import create_history_aware_retriever
from langchain.chains.retrieval import create_retrieval_chain
from langchain.chains.combine_documents import create_stuff_documents_chain
from langchain_core.runnables import RunnableLambda

from langchain.memory import ConversationBufferMemory
from langchain_core.chat_history import BaseChatMessageHistory
from langchain_core.runnables.history import RunnableWithMessageHistory

load_dotenv()

QDRANT_HOST = os.getenv("QDRANT_HOST", "localhost")
QDRANT_PORT = int(os.getenv("QDRANT_PORT", 6333))
COLLECTION_NAME = os.getenv("COLLECTION_NAME", "org-support-chat")

def get_rag_chain():

    embedding_model = get_gemini_embeddings()

    client = QdrantClient(host=QDRANT_HOST, port=QDRANT_PORT)
    vector_store = Qdrant(
        client= client,
        collection_name=COLLECTION_NAME,
        embeddings=embedding_model
    )

    retriever = vector_store.as_retriever(search_type="mmr", search_kwargs={"k": 5})

    llm = ChatGoogleGenerativeAI(
        model="gemini-2.0-flash",
        temperature=0.2,
        api_key=os.getenv("GOOGLE_API_KEY")
    )

    rephrase_prompt = ChatPromptTemplate.from_messages([
            ("system", "Rewrite the user question to be standalone using chat history"),
            MessagesPlaceholder("chat_history"),
            ("human", "{input}")
        ])
    
    history_retriever = create_history_aware_retriever(
        llm = llm,
        retriever= retriever,
        prompt=rephrase_prompt
    )

    qa_prompt = ChatPromptTemplate.from_messages([
        ("system", "You are a helpful assistant. Use the following context and prior conversation to answer the user's question."),
        ("system", "Context:\n{context}"),
        MessagesPlaceholder("chat_history"),
        ("human", "{input}"),
    ])

    doc_chain = create_stuff_documents_chain(llm = llm, prompt = qa_prompt)
    
    chain=create_retrieval_chain(
        retriever = history_retriever,
        combine_docs_chain = doc_chain
    )

    return chain


if __name__ == "__main__":
    from langchain_core.messages import HumanMessage, AIMessage

    chat_history = [
        HumanMessage(content="I ordered a software product last week approved by the company"),
        AIMessage(content="Sure, how can I assist?"),
    ]

    chain = get_rag_chain()
    response = chain.invoke({
        "input":"What is the deadline for me to return it?",
        "chat_history":chat_history
    })

    print(response)
    print("Answer :\n", response["answer"])