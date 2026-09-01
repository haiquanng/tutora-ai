"""
Ingestion pipeline KB Tutora: file bytes → extract → chunk → embed → store.

Luồng (admin upload file từ CMS → .NET proxy → endpoint /api/v1/kb/upload):
  1. detect định dạng (pdf/docx/xlsx) → extract_blocks
  2. chunk_blocks → list đoạn tự đủ nghĩa
  3. embed từng chunk (gemini-embedding-2, 768 — cùng không gian với match_tutora_kb)
  4. tạo 1 row tutora_kb_documents + N row tutora_kb_chunks

Cũng cung cấp list/delete document cho CMS quản lý nội dung.
"""
from __future__ import annotations

import asyncio
import re

from ...core.config import get_settings
from ...core.dependencies import get_supabase, get_gemini_client
from .extract import detect_source_type, extract_blocks
from .chunk import chunk_blocks
from ..telemetry.usage import track

_settings = get_settings()

GEMINI_EMBED_MODEL = "gemini-embedding-2"
# Batch nhỏ để tránh rate-limit embedding khi tài liệu nhiều chunk.
_EMBED_BATCH = 20
_EMBED_SLEEP = 2.0


def _embed_one(text: str) -> list[float]:
    gemini = get_gemini_client()
    with track("kb_ingest_embed", GEMINI_EMBED_MODEL) as _t:
        result = _t.done(gemini.models.embed_content(
            model=GEMINI_EMBED_MODEL,
            contents=text,
            config={"output_dimensionality": _settings.rag_embedding_dim},
        ))
    return result.embeddings[0].values


async def ingest_document(file_bytes: bytes, file_name: str, uploaded_by: str | None) -> dict:
    """Nạp 1 file → tài liệu + chunk trong DB. Trả {document_id, chunk_count, file_name}."""
    source_type = detect_source_type(file_name)  # raise UnsupportedFileType nếu lạ

    blocks = await asyncio.to_thread(extract_blocks, file_bytes, source_type)
    chunks = chunk_blocks(blocks)
    if not chunks:
        raise ValueError("Không đọc được nội dung text nào từ file.")

    sb = get_supabase()

    # 1. Tạo document (status=processing để CMS thấy đang chạy nếu file lớn).
    doc = (
        sb.table("tutora_kb_documents")
        .insert({
            "file_name": file_name,
            "source_type": source_type,
            "chunk_count": 0,
            "status": "processing",
            "uploaded_by": uploaded_by,
        })
        .execute()
        .data[0]
    )
    document_id = doc["id"]

    # 2. Embed + insert theo batch. Lỗi giữa chừng → đánh dấu failed, không để document treo.
    try:
        inserted = 0
        for start in range(0, len(chunks), _EMBED_BATCH):
            batch = chunks[start:start + _EMBED_BATCH]
            rows = []
            for i, content in enumerate(batch):
                embedding = await asyncio.to_thread(_embed_one, content)
                rows.append({
                    "document_id": document_id,
                    "title": file_name,
                    "content": content,
                    "chunk_index": start + i,
                    "embedding": embedding,
                })
            sb.table("tutora_kb_chunks").insert(rows).execute()
            inserted += len(rows)
            if start + _EMBED_BATCH < len(chunks):
                await asyncio.sleep(_EMBED_SLEEP)

        sb.table("tutora_kb_documents").update(
            {"chunk_count": inserted, "status": "ready"}
        ).eq("id", document_id).execute()
        return {"document_id": document_id, "chunk_count": inserted, "file_name": file_name}
    except Exception:
        sb.table("tutora_kb_documents").update({"status": "failed"}).eq("id", document_id).execute()
        raise


async def update_document_content(document_id: str, content: str) -> dict:
    """Admin sửa nội dung tài liệu (CMS) → chunk lại từ text mới → thay TOÀN BỘ chunk cũ +
    re-embed. Đổi source_type='manual' (đã sửa tay). Trả {document_id, chunk_count}."""
    text = (content or "").strip()
    if not text:
        raise ValueError("Nội dung rỗng.")

    # Tách theo đoạn (dòng trống) rồi chunk như bình thường — text sửa tay không có block
    # cấu trúc như file, nên coi mỗi đoạn cách nhau bởi dòng trống là 1 block.
    blocks = [b for b in re.split(r"\n\s*\n", text) if b.strip()]
    chunks = chunk_blocks(blocks or [text])
    if not chunks:
        raise ValueError("Không tạo được đoạn nội dung nào.")

    sb = get_supabase()
    # Xác nhận document tồn tại (tránh tạo chunk mồ côi nếu id sai).
    doc = sb.table("tutora_kb_documents").select("id").eq("id", document_id).limit(1).execute().data
    if not doc:
        raise ValueError("Không tìm thấy tài liệu.")

    sb.table("tutora_kb_documents").update({"status": "processing"}).eq("id", document_id).execute()
    try:
        # Xoá chunk cũ, embed + insert chunk mới.
        sb.table("tutora_kb_chunks").delete().eq("document_id", document_id).execute()
        inserted = 0
        for start in range(0, len(chunks), _EMBED_BATCH):
            batch = chunks[start:start + _EMBED_BATCH]
            rows = []
            for i, chunk_text in enumerate(batch):
                embedding = await asyncio.to_thread(_embed_one, chunk_text)
                rows.append({
                    "document_id": document_id,
                    "content": chunk_text,
                    "chunk_index": start + i,
                    "embedding": embedding,
                })
            sb.table("tutora_kb_chunks").insert(rows).execute()
            inserted += len(rows)
            if start + _EMBED_BATCH < len(chunks):
                await asyncio.sleep(_EMBED_SLEEP)

        sb.table("tutora_kb_documents").update(
            {"chunk_count": inserted, "status": "ready", "source_type": "manual"}
        ).eq("id", document_id).execute()
        return {"document_id": document_id, "chunk_count": inserted}
    except Exception:
        sb.table("tutora_kb_documents").update({"status": "failed"}).eq("id", document_id).execute()
        raise


async def list_documents() -> list[dict]:
    sb = get_supabase()
    return (
        sb.table("tutora_kb_documents")
        .select("id, file_name, source_type, chunk_count, status, created_at")
        .order("created_at", desc=True)
        .execute()
        .data or []
    )


async def delete_document(document_id: str) -> None:
    """Xoá tài liệu; chunk tự xoá theo (FK on delete cascade)."""
    sb = get_supabase()
    sb.table("tutora_kb_documents").delete().eq("id", document_id).execute()
