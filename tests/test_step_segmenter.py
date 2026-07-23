import asyncio

from app.services.step_segmenter import segment_steps
from app.services import solver_stream

# Lời giải mẫu đúng format TUTOR_SYSTEM_V2 quy định: "**Bước N: ...**", $$...$$,
# chốt "**Kết quả là:**" và blockquote mẹo nhớ.
SOLUTION = (
    "Bài này là phương trình bậc hai, mình cùng giải nhé.\n\n"
    "**Bước 1: Xác định hệ số**\n"
    "Phương trình có $a=1$, $b=-5$, $c=6$.\n\n"
    "**Bước 2: Tính delta**\n"
    "$$\\Delta = b^2 - 4ac = 1$$\n"
    "Vì $\\Delta > 0$ nên có hai nghiệm.\n\n"
    "**Kết quả là:** $x=2$ hoặc $x=3$\n\n"
    "> Mẹo: nhẩm Vi-et ra ngay 2 và 3."
)


def test_tach_du_cac_buoc():
    steps = segment_steps(SOLUTION)
    titles = [s["title"] for s in steps]
    assert titles == ["Phân tích đề", "Xác định hệ số", "Tính delta", "Kết luận"]

    # $$...$$ phải nằm ở formulas, không còn trong explanation.
    delta_step = steps[2]
    assert delta_step["formulas"] == ["\\Delta = b^2 - 4ac = 1"]
    assert "$$" not in delta_step["explanation"]

    # Đáp án + mẹo nhớ tách thành bước "Kết luận" riêng.
    assert "Kết quả là" in steps[-1]["explanation"]


def test_bai_ngan_khong_co_buoc():
    steps = segment_steps("**Đáp án: B.** $x=2$")
    assert len(steps) == 1
    assert steps[0]["title"] == "Lời giải"


def test_markdown_do_dang_khong_vo():
    # Đang stream: cắt ở mọi vị trí đều phải parse được, không raise.
    for n in range(1, len(SOLUTION) + 1, 7):
        segment_steps(SOLUTION[:n])


def test_tieu_de_khong_nhap_nhay_khi_stream():
    """Bước đã chốt (steps[:-1]) chỉ được xuất hiện với tiêu đề ĐẦY ĐỦ."""
    seen: set[str] = set()
    for n in range(1, len(SOLUTION) + 1):
        for step in segment_steps(SOLUTION[:n])[:-1]:
            seen.add(step["title"])
    # Nếu có nhấp nháy sẽ lọt vào các tiêu đề cụt kiểu "Tính d", "Tính de".
    assert seen == {"Phân tích đề", "Xác định hệ số", "Tính delta"}


def _part(**kw):
    """Tạo 1 part giả với các thuộc tính google-genai (text/exec/result)."""
    defaults = {
        "text": None,
        "executable_code": None,
        "code_execution_result": None,
    }
    defaults.update(kw)
    return type("Part", (), defaults)()


def _text_part(text):
    return _part(text=text)


def _chunk(parts):
    """Chunk shape thật: chunk.candidates[0].content.parts."""
    content = type("Content", (), {"parts": parts})()
    cand = type("Cand", (), {"content": content})()
    return type("Chunk", (), {"candidates": [cand], "text": None})()


class _FakeResp:
    def __init__(self, chunks):
        self._chunks = chunks

    def __iter__(self):
        for c in self._chunks:
            # Cho phép truyền sẵn chunk-đã-dựng, hoặc str (bọc thành 1 text part).
            yield c if not isinstance(c, str) else _chunk([_text_part(c)])


class _FakeClient:
    """Giả google-genai. Pha thinking dùng THINKING_SYSTEM, pha giải dùng system khác
    -> trả chunks riêng cho từng pha (phân biệt qua config.system_instruction)."""

    def __init__(self, solve_chunks, think_chunks=None):
        from app.utils.prompt import THINKING_SYSTEM

        def _stream(_self, **kw):
            cfg = kw.get("config")
            sys = getattr(cfg, "system_instruction", "") or ""
            if sys == THINKING_SYSTEM:
                return _FakeResp(think_chunks or [])
            return _FakeResp(solve_chunks)

        self.models = type("M", (), {"generate_content_stream": _stream})()


def _collect(response_format, chunks=None, is_problem=True, think_chunks=None):
    async def run():
        return [
            chunk
            async for chunk in solver_stream.solve_stream(
                client=_FakeClient(chunks if chunks is not None else [SOLUTION], think_chunks),
                question="q",
                message_id="m",
                session_id="s",
                is_problem=is_problem,
                response_format=response_format,
            )
        ]

    return asyncio.run(run())


def test_mac_dinh_markdown_khong_ro_ri_steps():
    """
    Zalo bot / mobile / AiChatService gom delta để lưu ChatHistory.
    Nếu field steps rò rỉ sang chế độ mặc định, chat sẽ hiện JSON cho người dùng.
    """
    chunks = _collect("markdown")
    assert all("steps" not in c and "steps_final" not in c for c in chunks)


def test_steps_giu_nguyen_markdown():
    """Bật steps KHÔNG được đổi nội dung markdown mà client cũ đang dựa vào."""
    md = "".join(c["delta"] for c in _collect("markdown"))
    st = "".join(c["delta"] for c in _collect("steps"))
    assert md == st

    final = [c for c in _collect("steps") if c["done"]][0]["steps_final"]
    assert [s["title"] for s in final] == [
        "Phân tích đề",
        "Xác định hệ số",
        "Tính delta",
        "Kết luận",
    ]


def _exec_result(outcome, output=""):
    return _part(
        code_execution_result=type("R", (), {"outcome": outcome, "output": output})()
    )


def _exec_code(code):
    return _part(executable_code=type("E", (), {"code": code})())


def test_thinking_pha_rieng_khong_lan_delta():
    """Pha 1 (THINKING_SYSTEM) -> field `thinking`; pha 2 (giải) -> delta. Tách bạch."""
    out = _collect(
        "markdown",
        chunks=[_text_part("**Đáp án: $x=2$**")],
        think_chunks=[_text_part("**Nhận dạng:** bài phương trình bậc hai...")],
    )
    thinking = "".join(c["thinking"] for c in out if c.get("thinking"))
    deltas = "".join(c.get("delta", "") for c in out)
    assert "nhận dạng" in thinking.lower()
    # Phần nghĩ tuyệt đối không rò sang delta (bot gom delta lưu ChatHistory).
    assert "nhận dạng" not in deltas.lower()
    assert "x=2" in deltas
    # Thinking phải tới TRƯỚC delta (pha 1 xong mới pha 2).
    kinds = [("thinking" if c.get("thinking") else "delta") for c in out if c.get("thinking") or c.get("delta")]
    assert kinds and kinds[0] == "thinking"
    assert "delta" in kinds and kinds.index("thinking") < kinds.index("delta")


def test_chat_thuong_khong_co_thinking():
    """is_problem=False (chat): KHÔNG chạy pha thinking -> chỉ có delta."""
    out = _collect("markdown", chunks=[_text_part("Chào bạn, mình là Tutora nhé!")], is_problem=False)
    deltas = "".join(c.get("delta", "") for c in out)
    assert deltas == "Chào bạn, mình là Tutora nhé!"
    assert all(not c.get("thinking") for c in out)


def test_code_execution_khong_lo_ra_client():
    """Code execution VẪN chạy (giúp đáp số đúng) nhưng KHÔNG stream ra client:
    đã bỏ hiển thị 'kiểm tra bằng máy tính'. Không có delta code, không có field verify."""
    chunks = [
        _chunk([_exec_code("print(2+3)")]),
        _chunk([_exec_result("OUTCOME_OK", "5")]),
        _chunk([_text_part("**Đáp án: $5$**")]),
    ]
    out = _collect("markdown", chunks=chunks)
    deltas = "".join(c.get("delta", "") for c in out)
    assert "print(" not in deltas and "5" in deltas
    assert all("verify" not in c for c in out)
    assert all("verified" not in c for c in out)
