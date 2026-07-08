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
    id: str        
    text: str      


class EmbedRequest(BaseModel):
    
    items: List[EmbedItem]


class EmbedResultItem(BaseModel):
    id: str
    embedding: Optional[List[float]] = None  
    error: Optional[str] = None              


class EmbedResponse(BaseModel):
    
    model: str
    dim: int
    results: List[EmbedResultItem]
