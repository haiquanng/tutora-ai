"""
Bài tập nhanh trong buổi học

Stateless — không ghi DB. BE sở hữu dữ liệu (learning_material_contents,
practice_sets/questions), ở đây chỉ đọc file / gọi Gemini rồi trả kết quả.

Dùng GEMINI_CLASSROOM_KEY riêng để quota lớp học không đụng quota /solve.
"""
from fastapi import APIRouter, Depends, UploadFile, File, HTTPException

from ..models.classroom import (
    MaterialExtractResponse,
    GeneratePracticeRequest,
    GeneratePracticeResponse,
)
from ..core.dependencies import get_classroom_gemini_client
from ..services.classroom import extract, generate

router = APIRouter(prefix="/api/v1")

# Tài liệu lớp chỉ nhận ảnh và PDF; 25MB đủ cho slide/đề cương thường gặp.
_MAX_BYTES = 25 * 1024 * 1024


@router.post("/materials/extract", response_model=MaterialExtractResponse)
async def extract_material(
    file: UploadFile = File(...),
    gemini=Depends(get_classroom_gemini_client),
):
    """Tài liệu (pdf/ảnh) -> toàn văn có mốc '[trang N]'.

    BE gọi NGẦM lúc gia sư upload, lưu vào learning_material_contents để lúc bấm
    "Tạo câu hỏi" giữa buổi dạy là có sẵn nội dung, không phải chờ parse.
    """
    file_bytes = await file.read()
    if not file_bytes:
        raise HTTPException(status_code=400, detail="File rỗng")
    if len(file_bytes) > _MAX_BYTES:
        raise HTTPException(status_code=413, detail="File quá lớn (>25MB)")

    kind = extract.detect_kind(file.filename or "", file.content_type)

    try:
        if kind == "pdf":
            full_text, page_count = extract.extract_pdf_text(file_bytes)
        else:
            full_text = await extract.extract_image_text(gemini, file_bytes)
            page_count = 1 if full_text else 0
    except Exception as e:
        print(f"extract_material error: {e}")
        return MaterialExtractResponse(error="Không đọc được nội dung tài liệu.")

    if not full_text.strip():
        # PDF scan không có text layer rơi vào đây — BE đánh dấu failed, gia sư biết
        # tài liệu này không dùng để sinh đề được.
        return MaterialExtractResponse(
            page_count=page_count or None,
            error="Tài liệu không có nội dung chữ đọc được.",
        )

    return MaterialExtractResponse(full_text=full_text, page_count=page_count or None)


@router.post("/practice/generate", response_model=GeneratePracticeResponse)
async def generate_practice(
    body: GeneratePracticeRequest,
    gemini=Depends(get_classroom_gemini_client),
):
    """Tài liệu đã chọn + yêu cầu gia sư -> bộ câu hỏi NHÁP.

    Nháp: gia sư đọc lại, sửa, rồi mới bấm gửi cho học sinh. AI không bao giờ chạm
    thẳng tới học sinh.
    """
    materials = [m.model_dump() for m in body.materials]
    result = await generate.generate_practice(gemini, materials, body.prompt)
    return GeneratePracticeResponse(**result)
