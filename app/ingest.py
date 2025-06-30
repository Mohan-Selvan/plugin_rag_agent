import os
from dotenv import load_dotenv
from langchain_community.document_loaders import PyMuPDFLoader
from langchain.text_splitter import RecursiveCharacterTextSplitter
from langchain_community.vectorstores import Qdrant
from qdrant_client import QdrantClient
from qdrant_client.http.models import VectorParams, Distance
from app.embeddings import get_gemini_embeddings


load_dotenv()

QDRANT_HOST = os.getenv('QDRANT_HOST', 'localhost')
QDRANT_PORT = int(os.getenv('QDRANT_PORT', 6333))
COLLECTION_NAME = os.getenv("COLLECTION_NAME", "org-support-chat")

def ingest_documents(filepath:str):
    print(f"Loading from path : {filepath}")
    loader = PyMuPDFLoader(filepath)
    documents = loader.load()

    splitter = RecursiveCharacterTextSplitter(chunk_size=500, chunk_overlap=50)
    chunks = splitter.split_documents(documents)
    print(f"🔹 {len(chunks)} chunks created.")

    # Embeddings
    embeddings = get_gemini_embeddings()

    # Qdrant client
    client = QdrantClient(host=QDRANT_HOST, port=QDRANT_PORT)

    client.recreate_collection(
        collection_name=COLLECTION_NAME,
        vectors_config=VectorParams(size=768, distance=Distance.COSINE)
    )

    vector_store = Qdrant(
        client=client,
        collection_name=COLLECTION_NAME,
        embeddings=embeddings
    )
    vector_store.add_documents(chunks)

    print("Ingestion complete")


if __name__ == "__main__":
    ingest_documents("data/org_policies.pdf")