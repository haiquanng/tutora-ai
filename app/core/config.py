from pydantic_settings import BaseSettings
from functools import lru_cache

class Settings(BaseSettings):
    gemini_api_key: str
    supabase_url: str
    supabase_key: str
    api_key: str
    docs_username: str = "tutora-ap"
    docs_password: str
    # "BAAI/bge-m3" — tốt hơn nhưng ~570MB, dùng khi có VPS riêng
    embed_model: str = "paraphrase-multilingual-MiniLM-L12-v2"
    rag_top_k: int = 3
    rag_embedding_dim: int = 768
    env: str = "development"

    class Config:
        env_file = ".env"

@lru_cache()
def get_settings() -> Settings:
    return Settings()
