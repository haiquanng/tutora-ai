"""
Bảng giá Gemini để quy token -> tiền.

VÌ SAO PHẢI TỰ TÍNH: SDK google-genai chỉ trả TOKEN (usage_metadata), không trả
tiền; và Google KHÔNG có API đọc chi phí theo API key. Muốn admin thấy "tốn bao
nhiêu" thì chỉ còn cách nhân token với bảng giá — nên bảng này phải được cập nhật
tay khi Google đổi giá.

Nguồn: https://ai.google.dev/gemini-api/docs/pricing (đọc 2026-08-31, standard tier).
Đơn vị: USD cho 1 TRIỆU token.

LƯU Ý về thinking: Google tính token "thinking" THEO GIÁ OUTPUT (trang giá ghi rõ
output đã bao gồm thinking tokens), nên _cost() cộng thoughts vào vế output.
"""
from __future__ import annotations

# input / output / cached-input cho 1M token.
# cached rẻ hơn input thường ~10 lần, phải tách vì prompt dài dùng cache nhiều.
_PRICES: dict[str, tuple[float, float, float]] = {
    "gemini-2.5-flash": (0.30, 2.50, 0.03),
    "gemini-2.5-flash-lite": (0.10, 0.40, 0.01),
    # Embedding chỉ có chiều input.
    "gemini-embedding-2": (0.15, 0.0, 0.0),
    "gemini-embedding-001": (0.15, 0.0, 0.0),
}

# Model lạ (Google đổi tên, ta thêm model mới mà quên cập nhật bảng) -> tính theo
# giá flash để KHÔNG âm thầm báo 0đ. Thà lệch còn hơn tưởng miễn phí.
_FALLBACK = _PRICES["gemini-2.5-flash"]

_MILLION = 1_000_000


def _rates(model: str) -> tuple[float, float, float]:
    """Khớp giá theo tên model, chấp nhận hậu tố phiên bản (vd '-preview-09-2025')."""
    if model in _PRICES:
        return _PRICES[model]
    # Khớp tiền tố dài nhất: 'gemini-2.5-flash-lite-preview' phải ra flash-lite,
    # không được rơi vào 'gemini-2.5-flash'.
    best = ""
    for key in _PRICES:
        if model.startswith(key) and len(key) > len(best):
            best = key
    return _PRICES[best] if best else _FALLBACK


def estimate_cost_usd(
    model: str,
    prompt_tokens: int,
    output_tokens: int,
    thoughts_tokens: int = 0,
    cached_tokens: int = 0,
) -> float:
    """Quy token ra USD. Trả 0.0 nếu không có token nào."""
    in_rate, out_rate, cached_rate = _rates(model)

    # prompt_tokens của Gemini ĐÃ bao gồm phần cached -> trừ ra để không tính 2 lần
    # (phần cached tính theo giá rẻ hơn).
    billable_input = max(0, prompt_tokens - cached_tokens)

    cost = (
        billable_input * in_rate
        + cached_tokens * cached_rate
        # thinking tính như output, xem docstring.
        + (output_tokens + thoughts_tokens) * out_rate
    ) / _MILLION

    return round(cost, 8)
