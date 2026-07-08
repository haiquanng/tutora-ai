"""
Schema cho API embed question bank.

.NET gửi CHỮ sang, tutora-ai trả VECTOR. tutora-ai stateless — KHÔNG đọc/ghi DB
question bank (question bank sống ở DB-A do .NET sở hữu). .NET ghi content vào
DB-A, gọi API này lấy vector, tự UPDATE cột embedding. Ranh giới sở hữu sạch:
tutora-ai chỉ là "máy embed".
"""
from pydantic import BaseModel
from typing import List, Optional


class EmbedItem(BaseModel):
    id: str        # question_id bên .NET — echo lại trong response để .NET map đúng row
    text: str      # thường là content + solution ghép lại (đề + lời giải mẫu)


class EmbedRequest(BaseModel):
    # Batch: 1 PDF ra ~20 câu -> .NET gửi 1 lần cả loạt, tránh 20 HTTP request lẻ.
    items: List[EmbedItem]


class EmbedResultItem(BaseModel):
    id: str
    embedding: Optional[List[float]] = None  # 768 số; null nếu row đó embed lỗi
    error: Optional[str] = None              # lý do lỗi cho row này (nếu có)


class EmbedResponse(BaseModel):
    # model + dim để .NET lưu kèm -> sau này đổi model còn biết row nào cần re-embed.
    model: str
    dim: int
    results: List[EmbedResultItem]
