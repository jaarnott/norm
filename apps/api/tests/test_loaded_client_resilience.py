"""Resilience of the pooled outbound HTTP layer (``app.connectors.http_pool``)
and the Loaded client that rides on it.

These pin the policy that keeps a transient Loaded blip from surfacing as a hard
failure (an empty units list on the Receive card, a failed receive) WITHOUT ever
re-sending a mutating request that might already have applied:

- a GET retries any transient (connect / timeout / 429 / 5xx);
- a write retries ONLY when the server provably never processed it — a connect
  error or a 429 — and never on a 5xx or a read-timeout;
- ``Retry-After`` is honored;
- the circuit breaker opens on sustained give-ups and short-circuits, but a plain
  4xx (our bad request, Loaded is up) never trips it.
"""

import httpx
import pytest

from app.connectors import http_pool
from app.services.circuit_breaker import CircuitBreaker


@pytest.fixture(autouse=True)
def _no_sleep(monkeypatch):
    # Never actually back off in tests.
    monkeypatch.setattr(http_pool, "_backoff", lambda attempt: 0.0)
    yield


def _install(monkeypatch, handler):
    """Point the shared pool at a MockTransport driven by ``handler`` and return a
    dict tracking how many requests it saw."""
    seen = {"n": 0}

    def _wrapped(request):
        seen["n"] += 1
        return handler(request, seen["n"])

    monkeypatch.setattr(
        http_pool,
        "_client",
        httpx.Client(transport=httpx.MockTransport(_wrapped)),
    )
    return seen


URL = "https://api.loadedhub.com/1.0/stock/internal/units"


# --- GET: retries every transient -------------------------------------------


def test_get_retries_429_then_succeeds(monkeypatch):
    def handler(request, n):
        if n < 3:
            return httpx.Response(429, headers={"Retry-After": "0"}, text="slow down")
        return httpx.Response(200, json={"units": [1]})

    seen = _install(monkeypatch, handler)
    resp = http_pool.send("GET", URL)
    assert resp.status_code == 200
    assert seen["n"] == 3


def test_get_retries_503_then_succeeds(monkeypatch):
    def handler(request, n):
        return httpx.Response(200 if n >= 2 else 503, json={"ok": True})

    seen = _install(monkeypatch, handler)
    resp = http_pool.send("GET", URL)
    assert resp.status_code == 200
    assert seen["n"] == 2


def test_get_gives_up_after_max_attempts(monkeypatch):
    def handler(request, n):
        return httpx.Response(500, text="boom")

    seen = _install(monkeypatch, handler)
    resp = http_pool.send("GET", URL)
    # Returns the final response rather than raising — the caller interprets it.
    assert resp.status_code == 500
    assert seen["n"] == http_pool._MAX_ATTEMPTS


def test_get_retries_read_timeout(monkeypatch):
    def handler(request, n):
        if n < 2:
            raise httpx.ReadTimeout("too slow", request=request)
        return httpx.Response(200, json={"ok": True})

    seen = _install(monkeypatch, handler)
    resp = http_pool.send("GET", URL)
    assert resp.status_code == 200
    assert seen["n"] == 2


# --- Writes: retried only when the server never processed the request --------


def test_write_not_retried_on_500(monkeypatch):
    def handler(request, n):
        return httpx.Response(500, text="boom")

    seen = _install(monkeypatch, handler)
    resp = http_pool.send("PUT", URL, json={"x": 1})
    # A 5xx on a write is ambiguous — it may already have applied. One attempt.
    assert resp.status_code == 500
    assert seen["n"] == 1


def test_write_not_retried_on_read_timeout(monkeypatch):
    def handler(request, n):
        raise httpx.ReadTimeout("slow", request=request)

    seen = _install(monkeypatch, handler)
    with pytest.raises(httpx.ReadTimeout):
        http_pool.send("PUT", URL, json={"x": 1})
    assert seen["n"] == 1


def test_write_retried_on_429(monkeypatch):
    def handler(request, n):
        if n < 2:
            return httpx.Response(429, headers={"Retry-After": "0"})
        return httpx.Response(200, json={"ok": True})

    seen = _install(monkeypatch, handler)
    # A 429 rejects the request before processing — safe to re-send a write.
    resp = http_pool.send("PUT", URL, json={"x": 1})
    assert resp.status_code == 200
    assert seen["n"] == 2


def test_write_retried_on_connect_error(monkeypatch):
    def handler(request, n):
        raise httpx.ConnectError("no route", request=request)

    seen = _install(monkeypatch, handler)
    # Connect error → never reached the server → safe to retry even a write.
    with pytest.raises(httpx.ConnectError):
        http_pool.send("PUT", URL, json={"x": 1})
    assert seen["n"] == http_pool._MAX_ATTEMPTS


# --- Circuit breaker ---------------------------------------------------------


def test_breaker_opens_and_short_circuits(monkeypatch):
    def handler(request, n):
        return httpx.Response(500, text="down")

    seen = _install(monkeypatch, handler)
    br = CircuitBreaker("loaded-test", failure_threshold=2, recovery_timeout=60.0)

    # Each give-up records ONE failure (not one per retry).
    http_pool.send("GET", URL, breaker=br)
    assert br.state == "closed"
    http_pool.send("GET", URL, breaker=br)
    assert br.state == "open"

    calls_before = seen["n"]
    with pytest.raises(httpx.ConnectError, match="circuit"):
        http_pool.send("GET", URL, breaker=br)
    # Open circuit refuses without touching the transport.
    assert seen["n"] == calls_before


def test_breaker_untripped_by_4xx(monkeypatch):
    def handler(request, n):
        return httpx.Response(404, text="not found")

    _install(monkeypatch, handler)
    br = CircuitBreaker("loaded-test", failure_threshold=2, recovery_timeout=60.0)

    # A 4xx means Loaded is up and our request was bad — the breaker stays shut
    # no matter how many arrive.
    for _ in range(5):
        resp = http_pool.send("GET", URL, breaker=br)
        assert resp.status_code == 404
    assert br.state == "closed"


def test_breaker_success_resets_failures(monkeypatch):
    state = {"fail": True}

    def handler(request, n):
        return httpx.Response(500 if state["fail"] else 200, json={})

    _install(monkeypatch, handler)
    br = CircuitBreaker("loaded-test", failure_threshold=2, recovery_timeout=60.0)

    http_pool.send("GET", URL, breaker=br)  # 1 failure
    state["fail"] = False
    http_pool.send("GET", URL, breaker=br)  # success resets the count
    state["fail"] = True
    http_pool.send("GET", URL, breaker=br)  # 1 failure again, not 2
    assert br.state == "closed"


# --- LoadedInvoiceClient maps outcomes to its existing contract --------------


def _bare_client():
    """A LoadedInvoiceClient without touching the DB — __init__ needs a spec and
    a Connection row, which these mapping tests don't exercise."""
    from app.services.received_invoice import LoadedInvoiceClient

    client = LoadedInvoiceClient.__new__(LoadedInvoiceClient)
    client._headers = {"Content-Type": "application/json"}
    client._auth = None
    return client


def test_client_raises_runtimeerror_on_4xx(monkeypatch):
    def handler(request, n):
        return httpx.Response(404, text="nope")

    _install(monkeypatch, handler)
    with pytest.raises(RuntimeError, match="404"):
        _bare_client().request("GET", "/x")


def test_client_raises_runtimeerror_on_transport_error(monkeypatch):
    def handler(request, n):
        raise httpx.ConnectError("no route", request=request)

    _install(monkeypatch, handler)
    with pytest.raises(RuntimeError, match="ConnectError"):
        _bare_client().request("GET", "/x")


def test_client_returns_json_on_success(monkeypatch):
    def handler(request, n):
        return httpx.Response(200, json={"units": ["Kilo", "Each"]})

    _install(monkeypatch, handler)
    assert _bare_client().request("GET", "/x") == {"units": ["Kilo", "Each"]}
