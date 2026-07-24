import asyncio
from typing import Optional, List, AsyncGenerator
from google import genai
from google.genai import types
from ..utils.prompt import (
    TUTOR_SYSTEM_V2,
    CHAT_SYSTEM,
    THINKING_SYSTEM,
    CANVAS_OPEN,
    CANVAS_CLOSE,
    _CANVAS_CONTENT_RULE,
    build_solve_prompt_v2,
)
from .step_segmenter import segment_steps

_THINK_MODEL = "gemini-2.5-flash-lite"
_SOLVE_MODEL = "gemini-2.5-flash"

_THINK_OPEN = "【SUY NGHĨ】"
_THINK_CLOSE = "【HẾT SUY NGHĨ】"
_ANSWER_MARK = "**Đáp án"



def _prefix_overlap(buf: str, markers: tuple[str, ...]) -> int:
    """Độ dài đuôi `buf` trùng ĐẦU của BẤT KỲ marker nào (giữ lại phòng thẻ cắt ngang chunk).
    Giữ đúng phần có thể là mảnh thẻ, còn lại phát ngay -> không trễ."""
    best = 0
    for marker in markers:
        max_len = min(len(buf), len(marker) - 1)
        for n in range(max_len, best, -1):
            if marker.startswith(buf[-n:]):
                best = n
                break
    return best


class _ThinkingSplitter:
    """
    Tách luồng text stream thành 2 mạch: 'thinking' (trong 【SUY NGHĨ】...【HẾT SUY NGHĨ】)
    và 'answer' (lời giải). Xử lý ONLINE từng chunk, chịu được thẻ cắt ngang giữa 2 chunk,
    và chịu được model QUÊN thẻ đóng (đóng dự phòng bằng "**Đáp án").

    feed(text) -> list[(kind, piece)] với kind ∈ {"thinking","answer"}.
    """

    def __init__(self) -> None:
        self._buf = ""
        self._in_thinking = False

    def _find_first(self, markers: tuple[str, ...]) -> tuple[int, str]:
        """Vị trí SỚM NHẤT trong buf khớp bất kỳ marker; (-1, '') nếu không có."""
        best_idx, best_marker = -1, ""
        for m in markers:
            i = self._buf.find(m)
            if i != -1 and (best_idx == -1 or i < best_idx):
                best_idx, best_marker = i, m
        return best_idx, best_marker

    def feed(self, text: str) -> list[tuple[str, str]]:
        self._buf += text
        out: list[tuple[str, str]] = []

        while True:
            if self._in_thinking:
                # Đóng bằng thẻ đóng HOẶC mốc lời giải (phòng model quên thẻ đóng).
                idx, marker = self._find_first((_THINK_CLOSE, _ANSWER_MARK))
                if idx == -1:
                    break
                before = self._buf[:idx]
                if before:
                    out.append(("thinking", before))
                # Nếu đóng bằng mốc lời giải thì GIỮ LẠI mốc (nó thuộc answer);
                # nếu đóng bằng thẻ 【HẾT】 thì nuốt thẻ đi.
                consume = len(marker) if marker == _THINK_CLOSE else 0
                self._buf = self._buf[idx + consume:]
                self._in_thinking = False
            else:
                idx = self._buf.find(_THINK_OPEN)
                if idx == -1:
                    break
                before = self._buf[:idx]
                if before:
                    out.append(("answer", before))
                self._buf = self._buf[idx + len(_THINK_OPEN):]
                self._in_thinking = True

        # Giữ đuôi nếu có thể là đầu của thẻ đang chờ (thẻ/mốc cắt ngang chunk).
        pending = (_THINK_CLOSE, _ANSWER_MARK) if self._in_thinking else (_THINK_OPEN,)
        hold = _prefix_overlap(self._buf, pending)
        if hold < len(self._buf):
            piece = self._buf[: len(self._buf) - hold]
            self._buf = self._buf[len(self._buf) - hold :]
            out.append(("thinking" if self._in_thinking else "answer", piece))
        return out

    def flush(self) -> list[tuple[str, str]]:
        """Cuối stream: phát nốt phần còn giữ trong buffer."""
        if not self._buf:
            return []
        piece, self._buf = self._buf, ""
        return [("thinking" if self._in_thinking else "answer", piece)]


class _CanvasSplitter:
    """
    Tách luồng lời giải thành 'chat' (hội thoại tự nhiên, hiện ở cột chat) và 'canvas'
    (nội dung học tập trong 【CANVAS】...【HẾT CANVAS】, hiện ở side panel) — model TỰ QUYẾT
    viết gì ở đâu trong 1 lượt sinh duy nhất, giống Claude Artifacts: có thể có chat cả
    TRƯỚC lẫn SAU khối canvas, không giả định cấu trúc nội dung (không đoán theo "Bước 1").

    Cùng cơ chế online/chịu-chunk-cắt-ngang với _ThinkingSplitter. Model quên thẻ đóng
    -> coi phần còn lại vẫn thuộc canvas tới hết stream (an toàn hơn để lộ nội dung canvas
    ra chat, vì canvas thường LÀ phần chính của câu trả lời khi có marker mở).

    feed(text) -> list[(kind, piece)] với kind ∈ {"chat","canvas"}.
    """

    def __init__(self) -> None:
        self._buf = ""
        self._in_canvas = False

    def feed(self, text: str) -> list[tuple[str, str]]:
        self._buf += text
        out: list[tuple[str, str]] = []

        while True:
            marker = CANVAS_CLOSE if self._in_canvas else CANVAS_OPEN
            idx = self._buf.find(marker)
            if idx == -1:
                break
            before = self._buf[:idx]
            if before:
                out.append(("canvas" if self._in_canvas else "chat", before))
            self._buf = self._buf[idx + len(marker):]
            self._in_canvas = not self._in_canvas

        # Giữ đuôi nếu có thể là đầu của marker đang chờ (cắt ngang giữa 2 chunk).
        pending = (CANVAS_CLOSE,) if self._in_canvas else (CANVAS_OPEN,)
        hold = _prefix_overlap(self._buf, pending)
        if hold < len(self._buf):
            piece = self._buf[: len(self._buf) - hold]
            self._buf = self._buf[len(self._buf) - hold:]
            out.append(("canvas" if self._in_canvas else "chat", piece))
        return out

    def flush(self) -> list[tuple[str, str]]:
        """Cuối stream: phát nốt phần còn giữ trong buffer."""
        if not self._buf:
            return []
        piece, self._buf = self._buf, ""
        return [("canvas" if self._in_canvas else "chat", piece)]


def _classify_part(part) -> tuple[str, str]:
    """
    Phân loại 1 part trong stream -> (kind, text).

    kind:
      "delta"     -> văn bản model (gồm cả <thinking>, tách ở bước sau)
      "code"      -> Gemini tự sinh Python kiểm tra đáp số
      "code_ok"   -> code chạy XONG không lỗi -> tín hiệu để suy ra verified
      "code_err"  -> code chạy LỖI -> tín hiệu verified=false
      ""          -> part rỗng/không liên quan, bỏ qua
    """
    # Kết quả chạy code: OUTCOME_OK => tính lại chạy trơn; ngược lại là lỗi/khác.
    result = getattr(part, "code_execution_result", None)
    if result is not None:
        outcome = getattr(result, "outcome", None)
        ok = outcome is None or "OK" in str(outcome).upper()
        return ("code_ok" if ok else "code_err", getattr(result, "output", "") or "")

    exec_code = getattr(part, "executable_code", None)
    if exec_code is not None:
        return ("code", getattr(exec_code, "code", "") or "")

    text = getattr(part, "text", None) or ""
    return ("delta", text) if text else ("", "")


def _iter_parts(chunk):
    """
    Lấy list part từ 1 chunk stream, chịu được cả shape thật của google-genai
    lẫn fake test (chỉ có .text). Trả list[(kind, text)].
    """
    candidates = getattr(chunk, "candidates", None)
    if candidates:
        content = getattr(candidates[0], "content", None)
        parts = getattr(content, "parts", None) if content else None
        if parts:
            return [_classify_part(p) for p in parts]
    # Fallback: chunk kiểu cũ chỉ phơi .text (bao gồm fake trong test).
    text = getattr(chunk, "text", None) or ""
    return [("delta", text)] if text else []


async def solve_stream(
    client: genai.Client,
    question: str,
    message_id: str,
    session_id: str,
    rag_chunks: Optional[List[dict]] = None,
    history: Optional[List[dict]] = None,
    is_problem: bool = True,
    bank_matches: Optional[List[dict]] = None,
    response_format: str = "markdown",
) -> AsyncGenerator[dict, None]:
    """
    Stream text tự nhiên theo phong cách gia sư, không JSON.

    response_format="steps": vẫn stream delta markdown như cũ (client cũ không gãy),
    nhưng kèm thêm "steps" đã tách cấu trúc để canvas web render — xem step_segmenter.
    """
    history_contents = []
    for msg in (history or []):
        role = "user" if msg["role"] == "user" else "model"
        history_contents.append(types.Content(role=role, parts=[types.Part(text=msg["content"])]))

    want_steps = response_format == "steps" and is_problem

    if is_problem:
        current_prompt = build_solve_prompt_v2(question, rag_chunks, bank_matches)
        # Nội dung lên canvas (note độc lập) -> cắt hẳn giọng điệu chat (chào/hỏi tu từ),
        # xem _CANVAS_CONTENT_RULE. Không ảnh hưởng câu trả lời chat thường.
        system = TUTOR_SYSTEM_V2 + _CANVAS_CONTENT_RULE if want_steps else TUTOR_SYSTEM_V2
    else:
        current_prompt = question
        system = CHAT_SYSTEM

    solve_contents = history_contents + [
        types.Content(role="user", parts=[types.Part(text=current_prompt)])
    ]

    loop = asyncio.get_event_loop()

    def _run_stream(contents, cfg, queue: asyncio.Queue, model: str = _SOLVE_MODEL):
        """Chạy 1 lời gọi stream trong thread, đẩy (kind, text) vào queue, kết bằng None."""
        def _worker():
            try:
                for chunk in client.models.generate_content_stream(
                    model=model, contents=contents, config=cfg
                ):
                    for kind, text in _iter_parts(chunk):
                        if kind:
                            loop.call_soon_threadsafe(queue.put_nowait, (kind, text))
            finally:
                loop.call_soon_threadsafe(queue.put_nowait, None)
        return loop.run_in_executor(None, _worker)

    def _thinking_chunk(text: str) -> dict:
        # Event RIÊNG: bot Zalo/mobile gom `delta` để lưu ChatHistory sẽ bỏ qua,
        # canvas web render khối "Đang suy nghĩ".
        return {"id": message_id, "session_id": session_id, "thinking": text, "done": False}

    # PHA 1: sinh RIÊNG phần suy nghĩ (chỉ khi giải toán)
    if is_problem:
        think_q: asyncio.Queue = asyncio.Queue()
        think_cfg = types.GenerateContentConfig(
            temperature=0.5,
            system_instruction=THINKING_SYSTEM,
            thinking_config=types.ThinkingConfig(thinking_budget=0),
        )
        think_prompt = f"[BÀI TOÁN]\n{question}"
        think_future = _run_stream(
            [types.Content(role="user", parts=[types.Part(text=think_prompt)])],
            think_cfg,
            think_q,
            model=_THINK_MODEL,  # thinking dùng flash-lite (rẻ, không cần verify)
        )
        while True:
            item = await think_q.get()
            if item is None:
                break
            kind, text = item
            if kind == "delta":  # phần suy nghĩ là text thường
                yield _thinking_chunk(text)
        await think_future

    # PHA 2: lời giải chính (bật code execution cho bài toán)
    solve_q: asyncio.Queue = asyncio.Queue()
    solve_cfg = types.GenerateContentConfig(
        temperature=0.5,
        top_p=1,
        system_instruction=system,
        thinking_config=types.ThinkingConfig(thinking_budget=0),
        # Code execution né lỗi MALFORMED_RESPONSE ở bài số thập phân/LaTeX nặng; không
        # stream code ra client (đã bỏ hiển thị verify) — xem xử lý kind code bên dưới.
        tools=[types.Tool(code_execution=types.ToolCodeExecution())] if is_problem else None,
    )
    solve_future = _run_stream(solve_contents, solve_cfg, solve_q)

    canvas_splitter = _CanvasSplitter() if want_steps else None
    canvas_accumulated = ""
    sent_steps = 0

    def _canvas_progress(piece: str) -> dict:
        nonlocal canvas_accumulated, sent_steps
        canvas_accumulated += piece
        steps = segment_steps(canvas_accumulated)
        complete = steps[:-1] if steps else []
        extra = {}
        if len(complete) > sent_steps:
            extra["steps"] = complete[sent_steps:]
            sent_steps = len(complete)
        return extra

    while True:
        item = await solve_q.get()
        if item is None:
            break
        kind, text = item

        # Code execution VẪN chạy (né malformed) nhưng KHÔNG stream ra client.
        if kind in ("code", "code_ok", "code_err"):
            continue

        if canvas_splitter is None:
            yield {"id": message_id, "session_id": session_id, "delta": text, "done": False}
            continue

        for piece_kind, piece in canvas_splitter.feed(text):
            if piece_kind == "chat":
                yield {"id": message_id, "session_id": session_id, "delta": piece, "done": False}
            else:
                chunk = {"id": message_id, "session_id": session_id, "delta": "", "done": False}
                chunk.update(_canvas_progress(piece))
                if "steps" in chunk:
                    yield chunk

    if canvas_splitter is not None:
        for piece_kind, piece in canvas_splitter.flush():
            if piece_kind == "chat":
                yield {"id": message_id, "session_id": session_id, "delta": piece, "done": False}
            else:
                _canvas_progress(piece)  # nuốt nốt, steps_final ở done_chunk sẽ đầy đủ

    await solve_future

    rag_used = bool(rag_chunks) or bool(bank_matches)
    done_chunk = {
        "id": message_id,
        "session_id": session_id,
        "delta": "",
        "done": True,
        "rag_used": rag_used,
    }
    if want_steps:
        # Chốt bằng danh sách ĐẦY ĐỦ: client thay thế toàn bộ, tránh lệch nếu có
        # delta nào rớt giữa chừng.
        done_chunk["steps_final"] = segment_steps(canvas_accumulated)
    yield done_chunk
