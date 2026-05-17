import base64
from google import genai
from google.genai import types

_OCR_PROMPT = (
    "Đọc và trích xuất NGUYÊN VĂN bài toán từ ảnh. "
    "Chuyển tất cả công thức sang LaTeX ($...$). "
    "Chỉ trả về đề bài, không giải."
)

async def extract_from_image(client: genai.Client, image_base64: str) -> str:
    """Gemini Vision OCR từ base64."""
    image_bytes = base64.b64decode(image_base64)
    response = client.models.generate_content(
        model="gemini-2.5-flash",
        contents=[
            types.Content(parts=[
                types.Part.from_bytes(data=image_bytes, mime_type="image/jpeg"),
                types.Part.from_text(text=_OCR_PROMPT),
            ])
        ]
    )
    return response.text.strip()


async def extract_from_url(client: genai.Client, image_url: str) -> str:
    """Gemini Vision OCR từ URL (Cloudinary hoặc bất kỳ URL công khai)."""
    response = client.models.generate_content(
        model="gemini-2.5-flash",
        contents=[
            types.Content(parts=[
                types.Part.from_uri(file_uri=image_url, mime_type="image/jpeg"),
                types.Part.from_text(text=_OCR_PROMPT),
            ])
        ]
    )
    return response.text.strip()
