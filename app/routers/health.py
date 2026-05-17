from fastapi import APIRouter
router = APIRouter()

@router.get("/")
async def root():
    return {"status": "ok"}

@router.get("/health")
async def health():
    return {"status": "ok", "service": "tutora response", "version": "1.0.0"}
