from fastapi import APIRouter, Depends, UploadFile, File, HTTPException

from ..models.extract import ExtractPdfResponse
from ..core.dependencies import get_gemini_client
from ..services.extract import extract_pdf

router = APIRouter(prefix="/api/v1")

_MAX_BYTES = 25 * 1024 * 1024   # ~25MB, khoảng giới hạn ≤20 trang


@router.post("/extract-pdf", response_model=ExtractPdfResponse)
async def extract_pdf_endpoint(
    file: UploadFile = File(...),
    gemini=Depends(get_gemini_client),
):
    """Nhận PDF (multipart), Gemini đọc -> list câu hỏi. Stateless, không ghi DB.
    BE tự validate số trang ≤20 trước khi gọi (giữ chất lượng extract)."""
    if file.content_type not in ("application/pdf", "application/octet-stream"):
        raise HTTPException(status_code=400, detail="Chỉ nhận file PDF")

    pdf_bytes = await file.read()
    if not pdf_bytes:
        raise HTTPException(status_code=400, detail="File rỗng")
    if len(pdf_bytes) > _MAX_BYTES:
        raise HTTPException(status_code=413, detail="File PDF quá lớn (>25MB)")

    return await extract_pdf(gemini, pdf_bytes)
