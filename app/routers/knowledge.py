from fastapi import APIRouter, UploadFile, File, Form, HTTPException
from pydantic import BaseModel

from ..services.knowledge.ingest import (
    ingest_document, list_documents, delete_document, update_document_content,
)
from ..services.knowledge.extract import UnsupportedFileType

router = APIRouter(prefix="/api/v1/kb")

# ~20MB — tài liệu chính sách thường nhỏ; chặn file quá lớn (embed lâu + tốn quota).
_MAX_BYTES = 20 * 1024 * 1024


@router.post("/upload")
async def upload_document(
    file: UploadFile = File(...),
    uploaded_by: str | None = Form(default=None),
):
    """CEO upload tài liệu Tutora (pdf/docx/xlsx) → extract → chunk → embed → KB.
    Gọi qua .NET proxy (admin CMS). Stateless: chỉ ghi bảng tutora_kb_*."""
    data = await file.read()
    if not data:
        raise HTTPException(status_code=400, detail="File rỗng")
    if len(data) > _MAX_BYTES:
        raise HTTPException(status_code=413, detail="File quá lớn (>20MB)")

    try:
        result = await ingest_document(data, file.filename or "unknown", uploaded_by)
    except UnsupportedFileType as e:
        raise HTTPException(status_code=400, detail=str(e))
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))
    return result


@router.get("/documents")
async def get_documents():
    """Danh sách tài liệu đã nạp (cho CMS quản lý/xoá)."""
    return {"documents": await list_documents()}


class UpdateContentBody(BaseModel):
    content: str


@router.put("/documents/{document_id}/content")
async def update_content(document_id: str, body: UpdateContentBody):
    """Admin sửa nội dung tài liệu (CMS) → chunk lại + re-embed toàn bộ."""
    try:
        return await update_document_content(document_id, body.content)
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))


@router.delete("/documents/{document_id}")
async def remove_document(document_id: str):
    """Xoá 1 tài liệu + toàn bộ chunk của nó (cascade)."""
    await delete_document(document_id)
    return {"ok": True, "document_id": document_id}
