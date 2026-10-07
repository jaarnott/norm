"""What a model call really used, and what it cost.

Anthropic reports a call's input in three parts: ``input_tokens`` (charged at
full price), ``cache_read_input_tokens`` and ``cache_creation_input_tokens``.
Norm stored only the first, so once the whole conversation was cached (4 Oct
2026) it recorded about 2 input tokens for a call that processed ~100,000 —
the thread and Settings figures read close to zero while the bill did not.

Two derived figures are stored on every call and every daily total:

``cost_usd``
    At the list price for that model, worked out when the call is recorded,
    so a later price change never rewrites history.

``billable_tokens``
    What plan limits count (decided 7 Oct 2026). Each token counts by its price
    relative to the model's normal input price: full-price input and output 1
    each — exactly what limits counted before — a cache read 0.05 on Opus 5.5
    (0.1 on most models) and a 5-minute cache write 1.25. So a cached
    conversation counts for what it costs to resend, not for nothing and not
    for every token in it.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class Price:
    """US dollars per million tokens."""

    input: float
    output: float
    cache_read: float
    cache_write: float  # 5-minute TTL — the only one Norm writes


#: Anthropic list prices (per million tokens), Oct 2026. Matched by prefix, so
#: a dated id ("claude-haiku-4-5-20251001") finds its family.
PRICES: dict[str, Price] = {
    "claude-opus-5-5": Price(4.00, 20.00, 0.20, 5.00),
    "claude-opus-5": Price(5.00, 25.00, 0.50, 6.25),
    "claude-opus-4-8": Price(5.00, 25.00, 0.50, 6.25),
    "claude-opus-4-7": Price(5.00, 25.00, 0.50, 6.25),
    "claude-opus-4-6": Price(5.00, 25.00, 0.50, 6.25),
    "claude-sonnet-5-5": Price(2.00, 10.00, 0.20, 2.50),
    "claude-sonnet-5": Price(2.00, 10.00, 0.20, 2.50),
    "claude-sonnet-4-6": Price(3.00, 15.00, 0.30, 3.75),
    "claude-haiku-4-5": Price(1.00, 5.00, 0.10, 1.25),
    "claude-fable-5-1": Price(10.00, 50.00, 0.25, 12.50),
    "claude-fable-5": Price(10.00, 50.00, 1.00, 12.50),
}

#: For an id with no entry: the agent default's price, so an unknown model is
#: never counted as free.
_FALLBACK = PRICES["claude-opus-5-5"]


def price_for(model: str | None) -> Price:
    name = (model or "").lower()
    # Longest prefix first: "claude-opus-5-5" before "claude-opus-5".
    for key in sorted(PRICES, key=len, reverse=True):
        if name.startswith(key):
            return PRICES[key]
    if name:
        logger.warning("no price for model %r — counting it at Opus 5.5 rates", model)
    return _FALLBACK


@dataclass(frozen=True)
class Usage:
    input: int = 0  # full-price input
    output: int = 0
    cache_read: int = 0
    cache_write: int = 0

    @classmethod
    def from_response(cls, usage) -> "Usage":
        """From an SDK ``response.usage`` (or None)."""
        if usage is None:
            return cls()

        def n(name: str) -> int:
            try:
                return int(getattr(usage, name, 0) or 0)
            except (TypeError, ValueError):
                return 0

        return cls(
            input=n("input_tokens"),
            output=n("output_tokens"),
            cache_read=n("cache_read_input_tokens"),
            cache_write=n("cache_creation_input_tokens"),
        )

    @property
    def total_input(self) -> int:
        """Every input token the model processed."""
        return self.input + self.cache_read + self.cache_write

    def __bool__(self) -> bool:
        return bool(self.input or self.output or self.cache_read or self.cache_write)


def cost_usd(model: str | None, usage: Usage) -> float:
    p = price_for(model)
    return (
        usage.input * p.input
        + usage.output * p.output
        + usage.cache_read * p.cache_read
        + usage.cache_write * p.cache_write
    ) / 1_000_000


def billable_tokens(model: str | None, usage: Usage) -> int:
    """Tokens as plan limits count them — see the module docstring."""
    p = price_for(model)
    return round(
        usage.input
        + usage.output
        + usage.cache_read * (p.cache_read / p.input)
        + usage.cache_write * (p.cache_write / p.input)
    )


def usage_fields(call) -> dict:
    """The usage of one stored ``LlmCall`` as the browser shows it."""
    cost = getattr(call, "cost_usd", None)
    return {
        "input_tokens": call.input_tokens,
        "output_tokens": call.output_tokens,
        "cache_read_tokens": getattr(call, "cache_read_tokens", None),
        "cache_write_tokens": getattr(call, "cache_write_tokens", None),
        "billable_tokens": getattr(call, "billable_tokens", None),
        "cost_usd": float(cost) if cost is not None else None,
    }
