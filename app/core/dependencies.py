from functools import lru_cache
from sentence_transformers import SentenceTransformer
from supabase import create_client, Client
from google import genai
from .config import get_settings

@lru_cache()
def get_embed_model() -> SentenceTransformer:
    settings = get_settings()
    return SentenceTransformer(settings.embed_model)

@lru_cache()
def get_supabase() -> Client:
    settings = get_settings()
    return create_client(settings.supabase_url, settings.supabase_key)

@lru_cache()
def get_gemini_client() -> genai.Client:
    settings = get_settings()
    return genai.Client(api_key=settings.gemini_api_key)
