from pydantic import field_validator
from pydantic_settings import BaseSettings
from functools import lru_cache

class Settings(BaseSettings):
    gemini_api_key: str
    # Key RIÊNG cho tính năng lớp học
    gemini_classroom_key: str = ""
    supabase_url: str
    supabase_key: str
    supabase_dev_url: str = ""
    supabase_dev_key: str = ""
    api_key: str
    docs_username: str = "tutora-ap"
    docs_password: str
    # "BAAI/bge-m3" — tốt hơn nhưng ~570MB, dùng khi có VPS riêng
    embed_model: str = "paraphrase-multilingual-MiniLM-L12-v2"
    rag_top_k: int = 3
    rag_embedding_dim: int = 768
    env: str = "development"
    # .NET BE — nguồn candidate cho tutor-chat (filter SQL + profile đầy đủ) và hồ sơ gia
    # sư công khai (tutoring_shared/tutor_api.py). Default = BE chạy local, khớp
    # VITE_BACKEND_URL trong .env.local của Tutora-FE; deploy thì set DOTNET_BE_URL.
    # KHÔNG để default trỏ prod: quên .env là app im lặng gọi thẳng dữ liệu thật.
    dotnet_be_url: str = "http://localhost:5166"

    # Cắt "/" cuối: mọi caller đều ghép f"{dotnet_be_url}/api/..." nên URL trong .env có
    # dấu / cuối sẽ tạo "//api/..." — ASP.NET Core không match route dạng đó, 404 sạch mọi
    # call sang .NET mà lại im lặng (caller nuốt exception, chỉ log).
    @field_validator("dotnet_be_url")
    @classmethod
    def _strip_trailing_slash(cls, v: str) -> str:
        return v.rstrip("/")

    class Config:
        env_file = ".env"

@lru_cache()
def get_settings() -> Settings:
    return Settings()
