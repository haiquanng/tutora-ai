from functools import lru_cache
# from sentence_transformers import SentenceTransformer
from supabase import create_client, Client
from google import genai
from .config import get_settings

# @lru_cache()
# def get_embed_model() -> SentenceTransformer:
#     settings = get_settings()
#     return SentenceTransformer(settings.embed_model)

def get_embed_model():
    return None

@lru_cache()
def get_supabase() -> Client:
    settings = get_settings()
    return create_client(settings.supabase_url, settings.supabase_key)


@lru_cache()
def get_supabase_dev() -> Client | None:
    """DB nghiệp vụ (.NET sở hữu) — nguồn sự thật cho tutor_profiles/prices.
    None nếu chưa cấu hình (vd môi trường chỉ chạy solve/RAG, không cần sync)."""
    settings = get_settings()
    if not settings.supabase_dev_url or not settings.supabase_dev_key:
        return None
    return create_client(settings.supabase_dev_url, settings.supabase_dev_key)

@lru_cache()
def get_gemini_client() -> genai.Client:
    settings = get_settings()
    return genai.Client(api_key=settings.gemini_api_key)
