from pydantic_settings import BaseSettings
from functools import lru_cache

class Settings(BaseSettings):
    gemini_api_key: str
    supabase_url: str
    supabase_key: str
    embed_model: str = "BAAI/bge-m3"
    rag_top_k: int = 3
    env: str = "development"

    class Config:
        env_file = ".env"

@lru_cache()
def get_settings() -> Settings:
    return Settings()
