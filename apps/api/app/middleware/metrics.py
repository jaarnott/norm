import time
from prometheus_client import Counter, Histogram, generate_latest, CONTENT_TYPE_LATEST
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

REQUEST_COUNT = Counter(
    "http_requests_total",
    "Total HTTP requests",
    ["method", "endpoint", "status"],
)

REQUEST_DURATION = Histogram(
    "http_request_duration_seconds",
    "HTTP request duration",
    ["method", "endpoint"],
    buckets=[0.01, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0, 30.0, 60.0],
)

LLM_CALL_DURATION = Histogram(
    "llm_call_duration_seconds",
    "LLM API call duration",
    ["model", "call_type", "status"],
    buckets=[0.5, 1.0, 2.5, 5.0, 10.0, 20.0, 30.0, 60.0],
)

LLM_CALL_COUNT = Counter(
    "llm_calls_total",
    "Total LLM API calls",
    ["model", "call_type", "status"],
)

LLM_TOKENS = Counter(
    "llm_tokens_total",
    "Total LLM tokens used",
    ["model", "direction"],  # direction: input or output
)

TOOL_CALL_DURATION = Histogram(
    "tool_call_duration_seconds",
    "Tool/connector call duration",
    ["connector", "action", "status"],
    buckets=[0.1, 0.5, 1.0, 2.5, 5.0, 10.0, 30.0],
)

TOOL_CALL_COUNT = Counter(
    "tool_calls_total",
    "Total tool/connector calls",
    ["connector", "action", "status"],
)


class MetricsMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        if request.url.path == "/metrics":
            return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)

        start = time.perf_counter()
        response = await call_next(request)
        duration = time.perf_counter() - start

        # Normalize path to avoid high-cardinality labels
        endpoint = request.url.path
        for prefix in ("/api/threads/", "/api/messages/", "/api/orders/"):
            if endpoint.startswith(prefix) and len(endpoint) > len(prefix):
                endpoint = prefix + "{id}"
                break

        REQUEST_COUNT.labels(
            method=request.method,
            endpoint=endpoint,
            status=response.status_code,
        ).inc()

        REQUEST_DURATION.labels(
            method=request.method,
            endpoint=endpoint,
        ).observe(duration)

        return response


def record_llm_call(
    model: str,
    call_type: str,
    status: str,
    duration_s: float,
    input_tokens: int = 0,
    output_tokens: int = 0,
):
    """Record LLM call metrics. Called from llm_interpreter.py."""
    LLM_CALL_COUNT.labels(model=model, call_type=call_type, status=status).inc()
    LLM_CALL_DURATION.labels(model=model, call_type=call_type, status=status).observe(
        duration_s
    )
    if input_tokens:
        LLM_TOKENS.labels(model=model, direction="input").inc(input_tokens)
    if output_tokens:
        LLM_TOKENS.labels(model=model, direction="output").inc(output_tokens)


def record_tool_call(connector: str, action: str, status: str, duration_s: float):
    """Record tool call metrics. Called from tool_loop.py or spec_executor.py."""
    TOOL_CALL_COUNT.labels(connector=connector, action=action, status=status).inc()
    TOOL_CALL_DURATION.labels(
        connector=connector, action=action, status=status
    ).observe(duration_s)
