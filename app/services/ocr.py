import base64
from google import genai
from google.genai import types

async def extract_from_image(client: genai.Client, image_base64: str) -> str:
    """Gemini Vision OCR: ảnh → text + LaTeX."""
    image_bytes = base64.b64decode(image_base64)
    response = client.models.generate_content(
        model="gemini-2.5-flash",
        contents=[
            types.Content(parts=[
                types.Part.from_bytes(data=image_bytes, mime_type="image/jpeg"),
                types.Part.from_text(text=
                    "Đọc và trích xuất NGUYÊN VĂN bài toán từ ảnh. "
                    "Chuyển tất cả công thức sang LaTeX ($...$). "
                    "Chỉ trả về đề bài, không giải."
                )
            ])
        ]
    )
    return response.text.strip()
