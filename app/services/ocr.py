import base64
import binascii
from google import genai
from google.genai import types

_OCR_PROMPT = (
    "Bạn là công cụ OCR đề Toán. Đọc và trích xuất NGUYÊN VĂN bài toán từ ảnh. "
    "Chuyển tất cả công thức sang LaTeX ($...$). Chỉ trả về đề bài, KHÔNG giải, KHÔNG thêm lời dẫn.\n"
    "QUAN TRỌNG: Nếu ảnh KHÔNG chứa đề toán đọc được (ảnh mờ, trống, không phải bài toán, "
    "không có chữ), trả về đúng một dòng: NO_MATH"
)

# Sentinel model trả về khi ảnh không có đề toán -> caller chặn, không đẩy vào solver.
NO_MATH = "NO_MATH"

# Magic bytes -> mime. Gemini dùng mime để chọn decoder; sai mime = đọc rác -> bịa đề.
_MIME_SIGNATURES = (
    (b"\xff\xd8\xff", "image/jpeg"),
    (b"\x89PNG\r\n\x1a\n", "image/png"),
    (b"GIF87a", "image/gif"),
    (b"GIF89a", "image/gif"),
    (b"RIFF", "image/webp"),   # RIFF....WEBP (kiểm tra thêm ở dưới)
    (b"BM", "image/bmp"),
)


def _detect_mime(image_bytes: bytes) -> str:
    """Đoán mime từ magic bytes. HEIC/HEIF nhận qua ftyp box. Mặc định jpeg."""
    if image_bytes[:4] == b"RIFF" and image_bytes[8:12] == b"WEBP":
        return "image/webp"
    # HEIC/HEIF: 'ftyp' ở offset 4, brand heic/heif/mif1/msf1...
    if image_bytes[4:8] == b"ftyp":
        brand = image_bytes[8:12]
        if brand in (b"heic", b"heix", b"hevc", b"heif", b"mif1", b"msf1"):
            return "image/heic"
    for sig, mime in _MIME_SIGNATURES:
        if mime == "image/webp":
            continue  # đã xử lý ở trên
        if image_bytes.startswith(sig):
            return mime
    return "image/jpeg"


def _validate_ocr(text: str) -> str:
    """Chuẩn hoá + kiểm tra output OCR. Trả NO_MATH nếu ảnh không có đề đọc được."""
    text = (text or "").strip()
    if not text or text.upper().replace("*", "").strip().startswith(NO_MATH):
        return NO_MATH
    return text


async def extract_from_image(client: genai.Client, image_base64: str) -> str:
    """Gemini Vision OCR từ base64. Trả NO_MATH nếu ảnh không phải đề toán."""
    try:
        image_bytes = base64.b64decode(image_base64, validate=True)
    except (binascii.Error, ValueError):
        return NO_MATH
    if not image_bytes:
        return NO_MATH
    mime = _detect_mime(image_bytes)
    try:
        response = client.models.generate_content(
            model="gemini-2.5-flash-lite",
            contents=[
                types.Content(parts=[
                    types.Part.from_bytes(data=image_bytes, mime_type=mime),
                    types.Part.from_text(text=_OCR_PROMPT),
                ])
            ]
        )
    except Exception:
        # Ảnh hỏng/không giải mã được -> coi như không đọc được đề, không giải bịa.
        return NO_MATH
    return _validate_ocr(response.text)


async def extract_from_url(client: genai.Client, image_url: str) -> str:
    """Gemini Vision OCR từ URL (Cloudinary hoặc bất kỳ URL công khai).

    Không hard-code mime nữa: tải bytes về, tự đoán mime rồi OCR như base64.
    Tránh việc Gemini đọc sai định dạng (PNG gửi nhãn jpeg) -> bịa đề bài."""
    import httpx

    try:
        async with httpx.AsyncClient(timeout=20.0, follow_redirects=True) as http:
            resp = await http.get(image_url)
            resp.raise_for_status()
            image_bytes = resp.content
    except Exception:
        return NO_MATH
    if not image_bytes:
        return NO_MATH

    mime = resp.headers.get("content-type", "").split(";")[0].strip()
    if not mime.startswith("image/"):
        mime = _detect_mime(image_bytes)
    try:
        response = client.models.generate_content(
            model="gemini-2.5-flash-lite",
            contents=[
                types.Content(parts=[
                    types.Part.from_bytes(data=image_bytes, mime_type=mime),
                    types.Part.from_text(text=_OCR_PROMPT),
                ])
            ]
        )
    except Exception:
        return NO_MATH
    return _validate_ocr(response.text)
