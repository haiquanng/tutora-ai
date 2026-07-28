"""Tag chủ đề (classification) đi kèm done event của solve_stream.
"""
import asyncio

from app.services.homework import solver_stream


class _FakeChunk:
    """Chunk kiểu cũ chỉ phơi .text — _iter_parts có nhánh fallback cho shape này."""

    def __init__(self, text: str):
        self.text = text
        self.candidates = None


class _FakeModels:
    def generate_content_stream(self, model, contents, config):
        return [_FakeChunk("Đáp số: x = 2 hoặc x = 3.")]


class _FakeClient:
    def __init__(self):
        self.models = _FakeModels()


def _collect(**kwargs) -> list[dict]:
    async def run():
        return [
            c
            async for c in solver_stream.solve_stream(
                client=_FakeClient(),
                question="Giải phương trình x^2 - 5x + 6 = 0",
                message_id="msg-1",
                session_id="sess-1",
                **kwargs,
            )
        ]

    return asyncio.run(run())


def _done(chunks: list[dict]) -> dict:
    return next(c for c in chunks if c.get("done"))


def test_classification_gan_vao_done_event():
    clf = {"grade": "9", "chapter": "can_bac_hai", "topic": "dai_so", "confidence": 0.87}
    done = _done(_collect(is_problem=True, classification=clf))

    assert done["classification"] == clf
    # Không được phá hợp đồng cũ: client Zalo/mobile vẫn đọc delta/done/rag_used.
    assert done["done"] is True
    assert done["delta"] == ""
    assert "rag_used" in done


def test_khong_co_classification_thi_khong_them_key():
    """Client cũ không nhận key lạ khi router không truyền tag (vd câu chào)."""
    done = _done(_collect(is_problem=False))

    assert "classification" not in done


def test_classification_khong_lam_hong_steps_final():
    """Canvas (response_format=steps) vẫn có steps_final khi kèm classification."""
    clf = {"grade": "10", "chapter": "menh_de", "topic": None, "confidence": 0.5}
    done = _done(
        _collect(is_problem=True, response_format="steps", classification=clf)
    )

    assert done["classification"] == clf
    assert "steps_final" in done
