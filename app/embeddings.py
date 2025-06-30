import os
from langchain_google_genai import GoogleGenerativeAIEmbeddings

def get_gemini_embeddings():
    return GoogleGenerativeAIEmbeddings(
        model="models/embedding-001",
        google_api_key=os.getenv("GOOGLE_API_KEY")
    )