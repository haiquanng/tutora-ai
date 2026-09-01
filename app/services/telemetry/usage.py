"""
Ghi nhận token/chi phí mỗi lời gọi Gemini rồi đẩy về .NET.
"""
from __future__ import annotations

import asyncio
import logging
import time
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Any, Optional

import httpx

from ...core.config import get_settings
from .pricing import estimate_cost_usd

logger = logging.getLogger(__name__)

# Gom sự kiện rồi gửi 1 lần: 1 phiên giải bài gọi Gemini nhiều lần (think, solve,
# classify, verify) — gửi lẻ sẽ thành 4 request mạng cho mỗi câu hỏi của học sinh.
_FLUSH_THRESHOLD = 20
_FLUSH_INTERVAL_SECONDS = 30
# Chặn hàng đợi phình vô hạn khi .NET chết: thà mất số liệu còn hơn hết RAM.
_MAX_QUEUE = 500

_queue: list[dict] = []
_lock = asyncio.Lock()
_last_flush = time.monotonic()
# Giữ tham chiếu các task gửi đang chạy — asyncio chỉ giữ weak reference nên task
# không ai cầm có thể bị GC thu giữa chừng.
_pending: set[asyncio.Task] = set()


@dataclass
class UsageRecord:
    """Số liệu bóc từ usage_metadata của một lời gọi."""

    feature: str
    model: str
    prompt_tokens: int = 0
    output_tokens: int = 0
    thoughts_tokens: int = 0
    cached_tokens: int = 0
    total_tokens: int = 0
    latency_ms: Optional[int] = None
    success: bool = True
    error: Optional[str] = None

    def to_payload(self) -> dict:
        return {
            "feature": self.feature,
            "model": self.model,
            "promptTokens": self.prompt_tokens,
            "outputTokens": self.output_tokens,
            "thoughtsTokens": self.thoughts_tokens,
            "cachedTokens": self.cached_tokens,
            "totalTokens": self.total_tokens,
            "costUsd": estimate_cost_usd(
                self.model,
                self.prompt_tokens,
                self.output_tokens,
                self.thoughts_tokens,
                self.cached_tokens,
            ),
            "latencyMs": self.latency_ms,
            "success": self.success,
            "error": self.error,
        }


def extract_usage(response: Any, feature: str, model: str) -> UsageRecord:
    """
    Bóc usage_metadata khỏi response Gemini.

    Tên trường theo google-genai; dùng getattr vì các bản SDK/model khác nhau có
    thể thiếu trường (vd embedding không có candidates_token_count).
    """
    meta = getattr(response, "usage_metadata", None)
    if meta is None:
        return UsageRecord(feature=feature, model=model)

    def _int(name: str) -> int:
        value = getattr(meta, name, None)
        return int(value) if value else 0

    prompt = _int("prompt_token_count")
    output = _int("candidates_token_count")
    thoughts = _int("thoughts_token_count")
    cached = _int("cached_content_token_count")
    total = _int("total_token_count") or (prompt + output + thoughts)

    return UsageRecord(
        feature=feature,
        model=model,
        prompt_tokens=prompt,
        output_tokens=output,
        thoughts_tokens=thoughts,
        cached_tokens=cached,
        total_tokens=total,
    )


async def record(usage: UsageRecord) -> None:
    """Xếp 1 sự kiện vào hàng đợi, gửi khi đủ lô hoặc quá hạn."""
    global _last_flush

    try:
        async with _lock:
            if len(_queue) >= _MAX_QUEUE:
                logger.warning("Hàng đợi ai usage đầy (%d), bỏ sự kiện.", _MAX_QUEUE)
                return
            _queue.append(usage.to_payload())

            due = (
                len(_queue) >= _FLUSH_THRESHOLD
                or time.monotonic() - _last_flush >= _FLUSH_INTERVAL_SECONDS
            )
            if not due:
                return

            batch = _queue[:]
            _queue.clear()
            _last_flush = time.monotonic()

        # Gửi ngoài lock, và không await để khỏi giữ chân request đang phục vụ.
        task = asyncio.create_task(_send(batch))
        _pending.add(task)
        task.add_done_callback(_pending.discard)
    except Exception as exc:  # đo đạc hỏng không được làm hỏng nghiệp vụ
        logger.warning("record ai usage lỗi: %s", exc)


def record_sync(usage: UsageRecord) -> None:
    """
    Bản đồng bộ của record(), dùng từ thread KHÔNG có event loop (worker của
    generate_content_stream).

    Gửi thẳng thay vì xếp lô: lời gọi stream vốn thưa (1 phiên giải bài vài lần),
    và quan trọng hơn — request SSE đóng ngay khi stream hết, nên mọi thứ hoãn lại
    đều có nguy cơ mất.
    """
    try:
        asyncio.run(_send([usage.to_payload()]))
    except Exception as exc:
        logger.warning("record_sync ai usage lỗi: %s", exc)


async def flush() -> None:
    """Đẩy nốt hàng đợi — gọi khi app shutdown để không mất số liệu."""
    async with _lock:
        if not _queue:
            return
        batch = _queue[:]
        _queue.clear()
    await _send(batch)


async def _send(batch: list[dict]) -> None:
    if not batch:
        return

    settings = get_settings()
    url = f"{settings.dotnet_be_url}/api/admin/ai-usage/events"
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.post(
                url,
                json={"events": batch},
                # Cùng khoá .NET dùng để gọi tutora-ai — không phát sinh secret mới.
                headers={"X-API-Key": settings.api_key},
            )
            if resp.status_code >= 400:
                logger.warning(
                    "Gửi ai usage thất bại %s: %s", resp.status_code, resp.text[:200]
                )
    except Exception as exc:
        # Mất số liệu đo đạc chấp nhận được; không retry để khỏi dồn tải khi BE sập.
        logger.warning("Gửi ai usage lỗi: %s", exc)


@contextmanager
def track(feature: str, model: str):
    """
    Đo 1 lời gọi Gemini đồng bộ.

    Dùng:
        with track("solve", MODEL) as t:
            resp = client.models.generate_content(...)
            t.done(resp)

    Không gọi done() (vì exception) thì vẫn ghi 1 sự kiện lỗi để đếm tỉ lệ hỏng.
    """
    tracker = _Tracker(feature, model)
    try:
        yield tracker
    except Exception as exc:
        tracker.fail(exc)
        raise
    finally:
        tracker.emit()


class _Tracker:
    def __init__(self, feature: str, model: str):
        self._feature = feature
        self._model = model
        self._started = time.monotonic()
        self._record: Optional[UsageRecord] = None
        self._emitted = False

    def done(self, response: Any) -> Any:
        """Ghi nhận response thành công. Trả lại chính response cho tiện gọi chuỗi."""
        self._record = extract_usage(response, self._feature, self._model)
        return response

    def set_usage(self, usage: UsageRecord) -> None:
        """Dùng cho stream: usage_metadata chỉ có ở chunk cuối, tự bóc rồi đưa vào."""
        self._record = usage

    def fail(self, exc: Exception) -> None:
        self._record = UsageRecord(
            feature=self._feature,
            model=self._model,
            success=False,
            error=f"{type(exc).__name__}: {exc}"[:500],
        )

    def emit(self) -> None:
        if self._emitted:
            return
        self._emitted = True

        rec = self._record or UsageRecord(
            feature=self._feature, model=self._model, success=False, error="no response"
        )
        rec.latency_ms = int((time.monotonic() - self._started) * 1000)

        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            # Thread không có event loop (worker của generate_content_stream) -> gửi thẳng.
            record_sync(rec)
            return

        # Giữ tham chiếu tới task: asyncio chỉ giữ WEAK reference, task chưa chạy
        # xong mà không ai cầm sẽ bị GC thu -> mất số liệu.
        task = loop.create_task(record(rec))
        _pending.add(task)
        task.add_done_callback(_pending.discard)
