"""Phân tích bài đánh giá đầu vào.

Stateless: chưa có internal-key nên AI KHÔNG gọi ngược vào .NET. Luồng là
FE lấy /analysis-input từ BE -> POST nguyên payload sang đây -> FE ghi kết quả
về BE /analysis (hoặc /analysis/failed nếu endpoint này lỗi).
"""
import json
import logging

from fastapi import APIRouter, Depends, HTTPException, Request

from ..core.dependencies import get_gemini_client
from ..core.limiter import limiter, RATE_LIMIT_PER_MINUTE, RATE_LIMIT_PER_HOUR
from ..models.assessment import AnalysisInput
from ..services.assessment import analyzer

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1")


@router.post("/analyze-assessment")
@limiter.limit(RATE_LIMIT_PER_MINUTE)
@limiter.limit(RATE_LIMIT_PER_HOUR)
async def analyze_assessment(
    request: Request,
    body: AnalysisInput,
    gemini=Depends(get_gemini_client),
):
    if not body.items:
        raise HTTPException(status_code=400, detail="Bài làm không có câu nào để phân tích")

    try:
        out = await analyzer.analyze(gemini, body)
    except json.JSONDecodeError as e:
        logger.exception("analyze-assessment: model trả JSON lỗi (attempt=%s)", body.attempt_id)
        raise HTTPException(status_code=502, detail=f"AI trả về dữ liệu không đọc được: {e}")
    except Exception as e:
        logger.exception("analyze-assessment thất bại (attempt=%s)", body.attempt_id)
        raise HTTPException(status_code=502, detail=f"AI phân tích thất bại: {e}")

    # Trả sẵn 2 dạng: `analysis` để FE render, `saveRequest` để POST thẳng vào
    # BE /analysis (BE nhận strengths/weaknesses/path là JSON string).
    return {
        "analysis": out.model_dump(),
        "saveRequest": {
            "summary": out.summary,
            "level": out.level,
            "strengths": json.dumps(out.strengths, ensure_ascii=False),
            "weaknesses": json.dumps(out.weaknesses, ensure_ascii=False),
            "recommendedPath": json.dumps(out.recommended_path, ensure_ascii=False),
            "analysisResult": json.dumps(out.model_dump(), ensure_ascii=False),
        },
    }
