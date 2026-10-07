"""Backfill the cache tokens, billable tokens, cost and organisation of past calls.

Until 7 Oct 2026 Norm stored only ``usage.input_tokens`` — the full-price part
of a call's input — so from 4 Oct, when the whole conversation was cached,
llm_calls and token_usage recorded ~0% of real input (x9y0z1a2b3c4). The
missing figures survive in one place: every agent (``tool_use``) call logged
``prompt_size`` to Cloud Logging with ``ctx_actual`` (= input_tokens),
``ctx_cache_read`` and ``ctx_cache_write`` — from 29 Sep 2026, when that log
line began.

1. Each log line is matched to its llm_calls row: same thread, same
   ``input_tokens``, recorded within ``--window`` seconds. Matched rows get
   cache_read/cache_write.
2. Every call since ``--since`` gets billable_tokens and cost_usd (cache parts
   where known, else input + output) and organization_id — the thread's
   user's organisation, or, for a call with no thread (invoice extraction,
   summarisation), the only organisation when there is exactly one.
3. token_usage is rebuilt per (organisation, user, day) from those calls. A
   day's recorded input/output that no remaining call accounts for (a deleted
   thread's calls, a rolled-back turn) still counts — at input + output for
   billable tokens and at the day's agent model price for cost — so nothing
   that was paid for disappears.

Usage (from apps/api, .env loaded; production through the 5435 proxy):
    gcloud logging read 'resource.type="cloud_run_revision" AND
      resource.labels.service_name="norm-api-production" AND
      textPayload:"prompt_size"' --project=norm-production-491101
      --freshness=30d --limit=100000 --format='value(timestamp,textPayload)'
      > prompt_size.txt
    DATABASE_URL=... uv run python scripts/backfill_llm_usage.py \\
        --log-file prompt_size.txt --since 2026-09-29 [--dry-run]
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import pathlib
import re
import sys
from collections import defaultdict
from decimal import Decimal

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

BACKUP_DIR = pathlib.Path.home() / "norm-split-backups"
_ANSI = re.compile(r"\x1b\[[0-9;]*m")
_FIELD = re.compile(r"(\w+)=(\S+)")


def parse_log_lines(lines) -> list[dict]:
    """``timestamp<TAB>payload`` lines → agent calls with their cache figures."""
    out = []
    for line in lines:
        ts, _, payload = line.rstrip("\n").partition("\t")
        text = _ANSI.sub("", payload).strip()
        # `prompt_size_on_error` is a call that failed — never billed, and it
        # carries no usage to match (the 5-6 Oct 2026 credit outage is full
        # of them).
        if text.split(" ", 1)[0] != "prompt_size":
            continue
        f = dict(_FIELD.findall(text))
        if f.get("call_type") != "tool_use" or not f.get("thread_id"):
            continue

        def n(k: str) -> int:
            try:
                return int(f.get(k) or 0)
            except ValueError:
                return 0

        try:
            at = dt.datetime.fromisoformat(ts.strip().replace("Z", "+00:00"))
        except ValueError:
            continue
        out.append(
            {
                "at": at,
                "thread_id": f["thread_id"],
                "input": n("ctx_actual"),
                "cache_read": n("ctx_cache_read"),
                "cache_write": n("ctx_cache_write"),
            }
        )
    return out


def match(calls: list[dict], logs: list[dict], window_s: float = 20.0) -> dict:
    """``{llm_call_id: log}`` — each log line to at most one call and back.

    ``calls``: dicts with id, thread_id, created_at, input_tokens. A pair must
    share the thread and full-price input count and lie within ``window_s``;
    the closest pair wins.
    """
    by_thread: dict[str, list[dict]] = defaultdict(list)
    for c in calls:
        by_thread[c["thread_id"]].append(c)
    pairs = []
    for i, log in enumerate(logs):
        for c in by_thread.get(log["thread_id"], ()):
            if (c.get("input_tokens") or 0) != log["input"]:
                continue
            gap = abs((c["created_at"] - log["at"]).total_seconds())
            if gap <= window_s:
                pairs.append((gap, i, c["id"]))
    pairs.sort()
    used_logs, out = set(), {}
    for _, i, cid in pairs:
        if i in used_logs or cid in out:
            continue
        used_logs.add(i)
        out[cid] = logs[i]
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--log-file", required=True)
    ap.add_argument("--since", default="2026-09-29")
    ap.add_argument("--window", type=float, default=20.0)
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    from sqlalchemy import func

    from app.db.engine import SessionLocal
    from app.db.models import (
        LlmCall,
        Organization,
        OrganizationMembership,
        Thread,
        TokenUsage,
    )
    from app.services.llm_cost import Usage, billable_tokens, cost_usd

    since = dt.datetime.fromisoformat(args.since).replace(tzinfo=dt.timezone.utc)
    logs = parse_log_lines(open(args.log_file, encoding="utf-8"))
    db = SessionLocal()
    try:
        orgs = [o.id for o in db.query(Organization.id).all()]
        only_org = orgs[0] if len(orgs) == 1 else None
        member_org = {
            m.user_id: m.organization_id for m in db.query(OrganizationMembership).all()
        }
        thread_user = dict(db.query(Thread.id, Thread.user_id).all())

        rows = db.query(LlmCall).filter(LlmCall.created_at >= since).all()
        agent = [
            {
                "id": r.id,
                "thread_id": r.thread_id,
                "created_at": r.created_at,
                "input_tokens": r.input_tokens,
            }
            for r in rows
            if r.call_type == "tool_use" and r.thread_id
        ]
        matched = match(agent, logs, args.window)
        print(
            f"{len(rows)} calls since {args.since}; {len(logs)} logged agent calls; "
            f"matched {len(matched)} of {len(agent)} agent calls"
        )

        # 1 + 2: every call's cache parts, billable, cost, organisation
        day_totals: dict[tuple, dict] = defaultdict(lambda: defaultdict(Decimal))
        day_model: dict[str, str] = {}
        day_cost: dict[str, Decimal] = defaultdict(Decimal)
        for r in rows:
            log = matched.get(r.id)
            usage = Usage(
                input=r.input_tokens or 0,
                output=r.output_tokens or 0,
                cache_read=log["cache_read"] if log else (r.cache_read_tokens or 0),
                cache_write=log["cache_write"] if log else (r.cache_write_tokens or 0),
            )
            user = thread_user.get(r.thread_id) if r.thread_id else None
            org = (
                r.organization_id
                or member_org.get(user)
                or (None if user else only_org)
            )
            if not args.dry_run:
                r.cache_read_tokens = usage.cache_read
                r.cache_write_tokens = usage.cache_write
                r.billable_tokens = billable_tokens(r.model, usage)
                r.cost_usd = cost_usd(r.model, usage)
                r.organization_id = org
            # A call with no usage (one that failed — the 5-6 Oct 2026 credit
            # outage) counts nowhere, as record_usage skips it.
            if org and usage:
                t = day_totals[(org, user, r.created_at.date().isoformat())]
                t["input_tokens"] += usage.input
                t["output_tokens"] += usage.output
                t["cache_read_tokens"] += usage.cache_read
                t["cache_write_tokens"] += usage.cache_write
                t["billable_tokens"] += billable_tokens(r.model, usage)
                t["cost_usd"] += Decimal(str(cost_usd(r.model, usage)))
                t["calls"] += 1
            if r.call_type == "tool_use":
                day_model[r.created_at.date().isoformat()] = r.model
            day_cost[r.created_at.date().isoformat()] += Decimal(
                str(cost_usd(r.model, usage))
            )

        # 3: daily totals
        existing = {
            (u.organization_id, u.user_id, u.date): u
            for u in db.query(TokenUsage).filter(TokenUsage.date >= args.since).all()
        }
        backup = {
            f"{k[0]}|{k[1]}|{k[2]}": {
                c.name: getattr(u, c.name) for c in TokenUsage.__table__.columns
            }
            for k, u in existing.items()
        }
        changes = 0
        for key in set(existing) | set(day_totals):
            org, user, day = key
            t = day_totals.get(key, {})
            u = existing.get(key)
            recorded_io = (u.input_tokens or 0) + (u.output_tokens or 0) if u else 0
            calls_io = int(t.get("input_tokens", 0) + t.get("output_tokens", 0))
            residual = max(0, recorded_io - calls_io)  # calls no longer on record
            price_model = day_model.get(day, "claude-opus-4-8")
            residual_cost = Decimal(str(cost_usd(price_model, Usage(input=residual))))
            want = {
                "cache_read_tokens": int(t.get("cache_read_tokens", 0)),
                "cache_write_tokens": int(t.get("cache_write_tokens", 0)),
                "billable_tokens": int(t.get("billable_tokens", 0)) + residual,
                "cost_usd": (t.get("cost_usd", Decimal(0)) + residual_cost).quantize(
                    Decimal("0.000001")
                ),
            }
            if u is None:
                want.update(
                    input_tokens=int(t.get("input_tokens", 0)),
                    output_tokens=int(t.get("output_tokens", 0)),
                    llm_call_count=int(t.get("calls", 0)),
                )
                print(
                    f"  new day total {day} org={org[:8]} user={(user or 'none')[:8]}: {want}"
                )
                if not args.dry_run:
                    db.add(
                        TokenUsage(organization_id=org, user_id=user, date=day, **want)
                    )
                changes += 1
                continue
            if any((getattr(u, k) or 0) != v for k, v in want.items()):
                changes += 1
                if not args.dry_run:
                    for k, v in want.items():
                        setattr(u, k, v)
        print(f"day totals to update/create: {changes}")

        for day in sorted(day_cost):
            print(f"  {day}  ${float(day_cost[day]):8,.2f}")
        cost = sum(float(t.get("cost_usd", 0)) for t in day_totals.values())
        print(f"cost of calls on record since {args.since}: ${cost:,.2f}")
        if args.dry_run:
            db.rollback()
            return
        BACKUP_DIR.mkdir(exist_ok=True)
        stamp = dt.datetime.now(dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        path = BACKUP_DIR / f"token-usage-before-backfill-{stamp}.json"
        path.write_text(json.dumps(backup, indent=1, default=str))
        print(f"backup {path}")
        db.commit()
        total = (
            db.query(func.sum(LlmCall.cost_usd))
            .filter(LlmCall.created_at >= since)
            .scalar()
        )
        print(
            f"committed; llm_calls cost since {args.since}: ${float(total or 0):,.2f}"
        )
    finally:
        db.close()


if __name__ == "__main__":
    main()
