# Scaling Norm for concurrent load

> Status: design / not yet built. Written 31 Aug 2026 against the current tree.
> Grounded in the actual code — file:line references throughout.

## Why now

Today there is essentially one production user. The moment there are ten — each
receiving 100 invoices at once — the platform falls over, because every heavy
operation runs **synchronously in the request, holds a scarce resource across a
slow Loaded call, and has no global concurrency limit.** Load scales with
`users × work` against three *fixed* ceilings:

1. **DB connections — the binding one.** `norm-prod-db` is `db-g1-small`,
   ~50 connections. The app engine lets **one instance** hold 22
   (`pool_size=10, max_overflow=12` — [engine.py:32-40](../apps/api/app/db/engine.py#L32-L40)).
   [engine.py:9-31](../apps/api/app/db/engine.py#L9-L31) spells the trap out:
   *"A POOL IS PER INSTANCE, AND THE DATABASE IS NOT … 22 × 3 is 66 against a
   db-g1-small serving ~50 … Keep maxScale at 2 on this tier, or raise the tier
   first."* That was never applied — prod `maxScale` is still 3.
2. **Loaded's API rate limit.** Global to the account, not per instance. We
   already see intermittent 429s.
3. **Per-instance compute** (the fan-outs, the LLM extraction).

The recurring "all stock units show as NEW" bug is one symptom of ceiling #1:
`/api/invoice-fixes/units` can't get a DB connection, 500s, the card falls back
to an empty unit list, and every line renders "— NEW". The sensei "died 3×"
crashes were another. One root, several faces.

### The core pathology (three parts, all confirmed in the tree)

- **Sessions held across slow Loaded I/O.**
  - Receive: `review_invoices`
    ([invoice_review.py:2368-2633](../apps/api/app/services/invoice_review.py#L2368-L2633))
    runs a **sequential loop over up to 200 invoices** — each doing several
    Loaded reads plus an optional `do_receive` write — entirely on the **one
    request-scoped `db` + one `config_db`**, held for the whole batch (minutes).
  - Consolidator fan-out: each `_worker`
    ([function_executor.py:503-512](../apps/api/app/connectors/function_executor.py#L503-L512))
    opens its own `SessionLocal()` and holds it across the full Loaded
    round-trip, ×20.
  - Tool-loop (×8) and extraction (×10) fan-outs do the same.
- **No global concurrency cap — anywhere.** Every request spins up its *own*
  `ThreadPoolExecutor` (8 / 20 / 10 / 10 / 8). There is **no `Semaphore`, no
  shared pool, no global work budget** in `app/`. Ten concurrent users multiply
  straight through against the 22-per-instance pool and the ~50-connection DB.
- **The least-defended dependency is under the widest fan-out.** The Loaded
  client ([received_invoice.py:140-156](../apps/api/app/services/received_invoice.py#L140-L156))
  has **no retry, no backoff, no rate limit, no circuit breaker, and no shared
  `httpx.Client`** — a fresh connection per call, and it raises immediately on
  any 429/5xx.

## What we already have to build on

Almost nothing here needs inventing. The primitives exist; they're just not yet
assembled into a load story:

| Need | Reuse | Where |
|---|---|---|
| Durable job queue, multi-instance-safe | `task_scheduler` — `FOR UPDATE SKIP LOCKED`, real columns, externally triggered | [task_scheduler.py:120-185](../apps/api/app/services/task_scheduler.py#L120-L185), [internal.py:27-47](../apps/api/app/routers/internal.py#L27-L47) |
| Off-request-path worker + progress UX | `sensei_runner` — JSON-column state machine, heartbeat, env-scoping, Cloud Run **job** dispatch | [sensei_runner.py](../apps/api/app/services/sensei_runner.py) |
| Shared cross-instance budget / rate limit | `check_rate_limit` — atomic Postgres fixed-window counter, no Redis | [mcp/ratelimit.py:29-66](../apps/api/app/mcp/ratelimit.py#L29-L66) |
| Circuit breaker for Loaded | `CircuitBreaker` (generic; only `anthropic_breaker` exists today) | [circuit_breaker.py:103-107](../apps/api/app/services/circuit_breaker.py#L103-L107) |
| Retry/backoff to port onto Loaded | `_is_transient_llm_error` + `_llm_retry_backoff` | [llm_interpreter.py:307-360](../apps/api/app/interpreter/llm_interpreter.py#L307-L360) |

We should stay on Postgres primitives (the counter + `SKIP LOCKED`) rather than
add Redis until they're proven insufficient — the multi-instance correctness is
already there without a new dependency.

## The design — five moves

### Move 0 — cap `maxScale = 2` (today, stopgap)

The fix [engine.py:9-31](../apps/api/app/db/engine.py#L9-L31) prescribes and that
was never applied. `2 × 22 = 44 < 50`, so today's occasional exhaustion stops.
Free, immediate. **Not the answer** — it *lowers* throughput, which is backwards
for scale. It only buys time to build the rest.

```
gcloud run services update norm-api-production \
  --region=australia-southeast1 --project=norm-production-491101 --max-instances=2
```

### Move 1 — make each unit of work cheap (pure win, low risk, no UX change)

Reduce the footprint of every operation so far more fit under the ceilings. Do
this first — it helps immediately, before any queue exists, and can't regress UX.

- **Harden the Loaded client** ([received_invoice.py:140-156](../apps/api/app/services/received_invoice.py#L140-L156)):
  - One **shared `httpx.Client`** with `httpx.Limits(max_connections=…,
    max_keepalive_connections=…)` — keep-alive pooling *and* a hard
    per-instance Loaded concurrency ceiling (right now it's a fresh TCP
    connection per call).
  - **Retry + backoff** on 429/5xx/timeout, honoring `Retry-After` — port the
    LLM pattern ([llm_interpreter.py:307-360](../apps/api/app/interpreter/llm_interpreter.py#L307-L360)).
  - Wrap calls in a **`loaded_breaker`** `CircuitBreaker` so a Loaded outage
    sheds fast instead of every worker blocking on doomed retries.
- **Stop holding DB sessions across Loaded I/O:**
  - Fan-out `_worker` ([function_executor.py:503-512](../apps/api/app/connectors/function_executor.py#L503-L512)):
    scope the session to the brief credential/spec read, **close it before** the
    Loaded HTTP call, re-open only to persist a result. Turns "20 sessions held
    for the whole fan-out" into "20 sessions held for a few ms each."
  - Receive `review_invoices` ([invoice_review.py:2498-2582](../apps/api/app/services/invoice_review.py#L2498-L2582)):
    check a session out **per invoice** (or per chunk) and release between,
    rather than pinning one request session across all 200. (Largely moot once
    receive is a background job — Move 3 — but valuable in the interim.)

### Move 2 — allocate the DB budget across services (the real cap for connections)

The DB's ~50 connections are the hard ceiling. Stop treating every instance as
entitled to 22; **split the budget by service** so peak demand provably fits:

- **API/request instances:** a *small* pool once heavy work moves to workers —
  e.g. `pool 5 + overflow 5`, `maxScale 2` → ≤ 20.
- **Worker service** (Move 3): the bulk — sized so
  `worker_maxScale × worker_pool ≤ remaining`.
- **Sum of every service's peak ≤ 50.** If that allocation is too tight (it will
  be once migrate/sensei jobs also draw), **raise the `norm-prod-db` tier** — a
  bigger tier means more RAM → higher `max_connections`, and the whole thing
  breathes. Recommended alongside the split.

This needs **no new primitive** — just right-sized pools + per-service maxScale.
The queue (Move 3) is what makes it *safe*: bursts pile into the queue instead of
spawning new instances that each grab another 22.

> Don't just shrink the app pool blindly — [engine.py:29](../apps/api/app/db/engine.py#L29)
> records that `5+7` once broke `get_pos_item_sales`, because the 20-way fan-out
> legitimately needs ~20 sessions *on whatever instance runs it*. The pool can
> only shrink on the API tier **after** the fan-out moves to the worker tier.

### Move 3 — move batch work off the request path (the queue + bounded workers)

This is the actual scaling move. Generalize `task_scheduler`'s durable queue into
a generic `work_jobs` table (`job_type`, `payload` JSON, `status`, `attempts`,
`heartbeat_at`, claimed via `FOR UPDATE SKIP LOCKED` — reusing its proven
multi-instance claim, [task_scheduler.py:120-155](../apps/api/app/services/task_scheduler.py#L120-L155)).

- **Batch receive becomes async.** `POST /receive` for a batch **enqueues** (one
  job per invoice, or per small chunk) and returns a `batch_id` immediately. A
  **bounded worker service** (`norm-worker-<env>`, modelled on the sensei Cloud
  Run job) drains the queue at a controlled concurrency. The UI polls batch
  progress — the same "studying…"-style card the sensei already uses.
- **Concurrency is now globally capped** by `#worker_instances ×
  per_worker_concurrency`, *independent of how many users submit.* 10 users ×
  100 invoices = 1,000 jobs draining at the worker rate — a deep queue, never a
  thundering herd.
- **Fairness:** claim round-robin by org/venue (the sensei's *"fairness beats
  parallelism"*, [sensei_runner.py:250-252](../apps/api/app/services/sensei_runner.py#L250-L252))
  so one big batch can't starve everyone else.
- **Single / interactive receive stays synchronous** — trivial load, no reason
  to make the user wait on a queue. Only *batches* go async.

The same queue later absorbs reconciliation and any other bursty, Loaded-heavy
job — one mechanism, many producers.

### Move 4 — a shared Loaded rate budget (the one genuinely-global limit)

Loaded's rate limit is global to the account, so per-instance `httpx.Limits`
isn't enough once several workers run. Add a **shared budget** on the
`check_rate_limit` Postgres counter ([mcp/ratelimit.py:29-66](../apps/api/app/mcp/ratelimit.py#L29-L66)):
every Loaded call — worker or request — acquires from a shared per-window budget
sized to Loaded's documented limit; over-budget calls wait/backoff. Multi-instance
correct, no Redis. Pair with the `loaded_breaker` from Move 1. Do this when
concurrent Loaded volume actually approaches the limit, not before.

## The UX change to call out

- **Single invoice:** unchanged — synchronous.
- **Batch (e.g. 100):** submit → *"receiving N invoices…"* → progress → done,
  reusing the sensei card pattern. This is the trade that makes receiving robust
  under load; the progress UI is worth designing deliberately rather than
  bolting on.

## Sequencing

| Phase | Move | Risk | Payoff |
|---|---|---|---|
| 0 (now) | maxScale=2 | none | stops today's exhaustion; buys time |
| 1 | Loaded client hardening + connection-hold fixes | low | biggest bang/buck; pure win, no UX change |
| 2 | DB budget allocation (+ maybe raise tier) | low | provable connection headroom |
| 3 | job queue + worker service + async batch receive + progress UI | medium | the real multi-user scaling |
| 4 | shared Loaded rate budget | low | needed only as Loaded volume climbs |

Start with **Phase 1**: it's low-risk, needs no UX change, and multiplies
effective capacity on its own — it may well carry you comfortably until Phase 3
is ready.

## What to measure

- `pg_stat_activity` peak `norm` connections during load (must stay under the
  DB's `max_connections`).
- 500 rate on `/api/invoice-fixes/units`; Loaded 429 rate; queue depth + drain
  rate; p95 receive latency.
- Load test the target: **10 users × 100 invoices concurrently** → no
  exhaustion, no all-"NEW" card, bounded Loaded 429s, queue drains steadily.

## Non-goals / notes

- Not adopting Redis yet — the Postgres primitives already in-tree
  (`check_rate_limit`, `SKIP LOCKED`) give multi-instance correctness without a
  new dependency. Revisit only if they're proven insufficient.
- The app-platform "door" path already downgrades `call_api_parallel` to
  sequential ([function_executor.py:784-790](../apps/api/app/connectors/function_executor.py#L784-L790))
  for audit-session safety — a precedent that the fan-out width is negotiable
  per context.
- This design settles several current symptoms at once (units-all-NEW, sensei
  crashes, receive latency under load) because they share the one root: no
  backpressure + resources held across slow I/O.
