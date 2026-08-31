"""Shared pooled HTTP client + retry for outbound connector calls.

Every outbound call to a connector API (LoadedHub today) went through a bare
``httpx.request(...)`` — a fresh TCP connection per call, and no retry on a
transient blip. Under load that is two problems:

- **No pooling, no ceiling.** A 20-way consolidator fan-out opened 20 fresh
  sockets; ten concurrent fan-outs, 200. Nothing bounded outbound concurrency
  from one instance, and every call paid a full TLS handshake.
- **No resilience.** A single 429 or 5xx surfaced as a hard failure — an empty
  units list on the Receive card, a failed receive — when a retry a moment later
  would have succeeded.

This module gives every outbound connector call ONE process-wide pooled client,
and gives the Loaded paths ONE retry policy:

- ``get_client()`` — a shared ``httpx.Client`` with ``Limits``. Keep-alive
  pooling reuses connections, and ``max_connections`` is a hard per-instance
  ceiling on outbound sockets, so the pool itself is a backpressure valve. It is
  sized to comfortably clear the widest fan-out (the consolidator's 20-way)
  without a call ever waiting on itself.
- ``send()`` — method-aware retry with backoff, honoring ``Retry-After``, and an
  optional circuit breaker. A GET is idempotent and retries on any transient
  (connect error / timeout / 429 / 5xx). A non-idempotent write retries ONLY when
  we know the server never processed it — a connect error (never reached it) or a
  429 (rejected before processing) — and NEVER on a 5xx or a read-timeout, which
  could mean the receive already applied and a retry would double-apply it.

``httpx.Client`` is safe to share across threads for issuing requests, so the one
client serves the fan-out's ThreadPoolExecutor workers directly.
"""

from __future__ import annotations

import random
import threading
import time

import httpx

# Per-instance outbound ceiling. Keep-alive pooling makes ``max_connections`` the
# concurrency cap on outbound connector calls from ONE API instance. It must sit
# comfortably above the widest fan-out (the consolidator's 20-way) so a single
# fan-out never blocks on itself; beyond that, waiting for a socket is the
# backpressure we want rather than opening unbounded sockets.
_MAX_CONNECTIONS = 30
_MAX_KEEPALIVE = 15

# Split connect/read/write so a slow-to-accept host fails fast on connect while a
# genuinely slow response still gets its full read budget.
_DEFAULT_TIMEOUT = httpx.Timeout(connect=10.0, read=30.0, write=30.0, pool=10.0)

# Small: a retry only rescues a passing blip. A persistent failure should surface
# quickly, not stall a request behind three backoffs.
_MAX_ATTEMPTS = 3

_IDEMPOTENT = frozenset({"GET", "HEAD", "OPTIONS"})

_client: httpx.Client | None = None
_client_lock = threading.Lock()


def get_client() -> httpx.Client:
    """The process-wide pooled client. Lazily built, thread-safe."""
    global _client
    c = _client
    if c is None:
        with _client_lock:
            if _client is None:
                _client = httpx.Client(
                    limits=httpx.Limits(
                        max_connections=_MAX_CONNECTIONS,
                        max_keepalive_connections=_MAX_KEEPALIVE,
                    ),
                    timeout=_DEFAULT_TIMEOUT,
                )
            c = _client
    return c


def _backoff(attempt: int) -> float:
    """Seconds before retrying ``attempt`` (0-based): exponential + jitter, so a
    fleet of concurrent workers hitting the same 429 don't retry in lockstep."""
    return min(8.0, 0.5 * (2**attempt)) + random.uniform(0, 0.3)


def _retry_after(resp: httpx.Response) -> float | None:
    """Honor a ``Retry-After`` header when it's a plain seconds count, capped so a
    server can't park a worker for minutes. The HTTP-date form is rare from
    Loaded — fall back to our own backoff for it."""
    ra = resp.headers.get("Retry-After")
    if not ra:
        return None
    try:
        return min(30.0, float(ra))
    except (TypeError, ValueError):
        return None


def send(
    method: str,
    url: str,
    *,
    headers: dict | None = None,
    json: object = None,
    content: object = None,
    auth: object = None,
    timeout: float | httpx.Timeout | None = None,
    breaker=None,
) -> httpx.Response:
    """Send one request through the shared pool, retrying transient failures.

    Returns the ``httpx.Response`` whatever its status — the caller owns
    status-code interpretation. Raises the underlying ``httpx`` error only when a
    transport failure survives every allowed retry (or the breaker is open).
    """
    method_u = method.upper()
    idempotent = method_u in _IDEMPOTENT
    client = get_client()

    if breaker is not None and not breaker.allow_request():
        # Fail fast instead of piling another call onto a service we already
        # believe is down. Surfaced as a transport error so callers treat it like
        # any other Loaded outage.
        raise httpx.ConnectError(
            f"circuit '{getattr(breaker, 'name', '?')}' open — refusing {method_u} call"
        )

    kw: dict = {}
    if timeout is not None:
        kw["timeout"] = timeout

    last_exc: Exception | None = None
    for attempt in range(_MAX_ATTEMPTS):
        is_last = attempt == _MAX_ATTEMPTS - 1
        try:
            resp = client.request(
                method_u,
                url,
                headers=headers,
                json=json,
                content=content,
                auth=auth,
                **kw,
            )
        except (httpx.ConnectError, httpx.ConnectTimeout) as exc:
            # Never reached the server → safe to retry ANY method.
            last_exc = exc
            if is_last:
                if breaker is not None:
                    breaker.record_failure()
                raise
            time.sleep(_backoff(attempt))
            continue
        except (httpx.TimeoutException, httpx.TransportError) as exc:
            # Ambiguous: a write may have been processed before the failure →
            # retry a GET only.
            last_exc = exc
            if is_last or not idempotent:
                if breaker is not None:
                    breaker.record_failure()
                raise
            time.sleep(_backoff(attempt))
            continue

        status = resp.status_code
        retryable_status = status == 429 or status >= 500
        # A 5xx on a write is ambiguous (may have applied) — only a 429 (rejected
        # before processing) is safe to retry. A GET may retry any transient.
        may_retry = retryable_status and (idempotent or status == 429)
        if may_retry and not is_last:
            time.sleep(_retry_after(resp) or _backoff(attempt))
            continue

        if breaker is not None:
            # Giving up on a 429/5xx is a Loaded-side failure; a 4xx means Loaded
            # is up and our request was bad — that keeps the breaker closed.
            if retryable_status:
                breaker.record_failure()
            else:
                breaker.record_success()
        return resp

    # Only reachable if every attempt raised a retryable transport error on a
    # path that also allowed a retry — re-raise the last one.
    raise last_exc if last_exc else RuntimeError("send: retries exhausted")
