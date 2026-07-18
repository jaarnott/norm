"""Rate limiting for the MCP surface.

The app's existing limiter is unusable here: it keys on remote IP, and every
claude.ai request arrives from Anthropic's egress — so all Norm customers would
share one bucket. It's also in-memory, i.e. per-instance, which on Cloud Run
means N× the intended limit.

So: Postgres fixed-window counters via an atomic upsert. Correct across any
number of instances with no coordination, and no new infra (Redis would be
~$50/mo/env + a VPC connector + a new way for the API to fall over, for volume
v1 doesn't have). ``check_rate_limit`` is one function with one signature —
swap the body for Redis later if volume demands, callers unchanged.
"""

from __future__ import annotations

from datetime import datetime, timezone

from fastapi import HTTPException
from sqlalchemy import text
from sqlalchemy.orm import Session


def _window_start(now: datetime, window_seconds: int) -> datetime:
    epoch = int(now.timestamp())
    return datetime.fromtimestamp(epoch - (epoch % window_seconds), tz=timezone.utc)


def check_rate_limit(
    db: Session,
    bucket_key: str,
    limit: int,
    window_seconds: int,
    *,
    now: datetime | None = None,
) -> None:
    """Increment a fixed-window counter; raise 429 if it exceeds ``limit``.

    The atomic ``INSERT ... ON CONFLICT DO UPDATE ... RETURNING count`` is the
    whole trick: one round trip, correct under any concurrency, no read-then-
    write race across instances.
    """
    now = now or datetime.now(timezone.utc)
    window = _window_start(now, window_seconds)

    row = db.execute(
        text(
            "INSERT INTO mcp_rate_limits (id, bucket_key, window_start, count) "
            "VALUES (gen_random_uuid()::text, :key, :window, 1) "
            "ON CONFLICT (bucket_key, window_start) "
            "DO UPDATE SET count = mcp_rate_limits.count + 1 "
            "RETURNING count"
        ),
        {"key": bucket_key, "window": window},
    ).first()
    db.commit()

    count = row[0] if row else 1
    if count > limit:
        retry_after = int((window.timestamp() + window_seconds) - now.timestamp())
        raise HTTPException(
            status_code=429,
            detail="Rate limit exceeded",
            headers={"Retry-After": str(max(retry_after, 1))},
        )


# Layered buckets. Enforced inside the auth/dispatch path so no endpoint can
# forget one. (key, limit, window_seconds)
def enforce_call_limits(db: Session, principal) -> None:
    """Per-token, per-user, and per-org call ceilings for a tool/RPC call."""
    check_rate_limit(
        db, f"tok:{principal.token_id or principal.client_id}:call", 120, 60
    )
    check_rate_limit(db, f"usr:{principal.user_id}:call", 300, 60)
    check_rate_limit(db, f"org:{principal.organization_id}:call", 2000, 60)


def enforce_ip_limit(
    db: Session, ip: str, bucket: str, limit: int, window: int
) -> None:
    """For the pre-auth endpoints (register, token, consent) keyed by IP."""
    check_rate_limit(db, f"ip:{ip}:{bucket}", limit, window)


def gc_expired(db: Session, older_than_seconds: int = 3600) -> int:
    """Delete windows older than an hour. Called by /internal/mcp-gc."""
    now = datetime.now(timezone.utc)
    cutoff = datetime.fromtimestamp(
        now.timestamp() - older_than_seconds, tz=timezone.utc
    )
    n = db.execute(
        text("DELETE FROM mcp_rate_limits WHERE window_start < :cutoff"),
        {"cutoff": cutoff},
    ).rowcount
    db.commit()
    return n
