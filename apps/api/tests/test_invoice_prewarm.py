"""The background extraction pre-warm: bounds, safety, and failure memory.

Every test here names the regression it exists to catch. The feature's two
failure modes are both silent — it can quietly stop working (and nobody
notices, because the fallback is just today's slow open), or it can quietly
grow into the unbounded sequential loop docs/scaling-and-load.md warns about.
"""

import datetime as _dt

import pytest

from app.services import invoice_prewarm as PW


class _Row:
    """A stand-in for the InvoicePrewarmAttempt ORM row."""

    def __init__(self, invoice_id, *, warmed_at=None, attempts=0, next_attempt_at=None):
        self.venue_id = "v-1"
        self.invoice_id = invoice_id
        self.warmed_at = warmed_at
        self.attempts = attempts
        self.next_attempt_at = next_attempt_at
        self.last_error = None


class TestTheBoundsThatMustNeverRegress:
    def test_only_a_handful_of_extractions_per_tick(self, monkeypatch):
        """Fails if someone removes the cap, or drifts it toward the
        200-invoice sequential loop the scaling doc warns about."""
        assert PW.PREWARM_MAX_PER_TICK <= 8, "the cap is the whole safety story"

        calls: list[str] = []
        monkeypatch.setattr(PW, "_loadedhub_venue_ids", lambda db: ["v-1"])
        monkeypatch.setattr(
            PW, "_candidates", lambda db, cdb, v: [f"inv-{i}" for i in range(50)]
        )
        monkeypatch.setattr(PW, "warm_one", lambda v, i: (calls.append(i), "warmed")[1])
        monkeypatch.setattr(PW, "SessionLocal", None, raising=False)

        out = _run_tick(monkeypatch)
        assert out["warmed"] == PW.PREWARM_MAX_PER_TICK
        assert len(calls) == PW.PREWARM_MAX_PER_TICK

    def test_a_cache_hit_does_not_consume_the_budget(self, monkeypatch):
        """Fails if cache hits start counting. They cost one cheap Loaded call;
        letting them eat the budget means a venue whose invoices are mostly
        warm never gets to the cold ones."""
        seen: list[str] = []

        def warm(v, i):
            seen.append(i)
            return "cached" if len(seen) <= 20 else "warmed"

        monkeypatch.setattr(PW, "_loadedhub_venue_ids", lambda db: ["v-1"])
        monkeypatch.setattr(
            PW, "_candidates", lambda db, cdb, v: [f"inv-{i}" for i in range(40)]
        )
        monkeypatch.setattr(PW, "warm_one", warm)

        out = _run_tick(monkeypatch)
        assert out["cached"] == 20
        assert out["warmed"] == PW.PREWARM_MAX_PER_TICK

    def test_the_deadline_stops_between_invoices(self, monkeypatch):
        """Fails if the wall-clock guard is dropped or moved somewhere it
        cannot fire."""
        clock = {"t": 0.0}
        monkeypatch.setattr(PW.time, "monotonic", lambda: clock["t"])

        def warm(v, i):
            clock["t"] += PW.PREWARM_DEADLINE_SECONDS  # one very slow invoice
            return "warmed"

        monkeypatch.setattr(PW, "_loadedhub_venue_ids", lambda db: ["v-1"])
        monkeypatch.setattr(
            PW, "_candidates", lambda db, cdb, v: [f"inv-{i}" for i in range(10)]
        )
        monkeypatch.setattr(PW, "warm_one", warm)

        out = _run_tick(monkeypatch)
        assert out["warmed"] == 1, "the deadline must stop the tick after one"

    def test_venues_are_interleaved_not_drained_one_at_a_time(self, monkeypatch):
        """Fails if round-robin goes: one venue's backlog would then starve
        every other venue indefinitely."""
        order: list[str] = []
        monkeypatch.setattr(PW, "_loadedhub_venue_ids", lambda db: ["v-1", "v-2"])
        monkeypatch.setattr(
            PW,
            "_candidates",
            lambda db, cdb, v: [f"{v}-a", f"{v}-b", f"{v}-c"],
        )
        monkeypatch.setattr(PW, "warm_one", lambda v, i: (order.append(v), "warmed")[1])

        _run_tick(monkeypatch)
        assert order[0] != order[1], "the first two must come from different venues"


class TestFailureMemory:
    def test_a_failing_invoice_backs_off_then_stops_forever(self):
        """Fails if the starvation guard goes. A permanently-unreadable
        invoice is newest-first every tick, so without a terminal state a few
        of them stall the feature while it looks perfectly healthy."""
        now = PW._now()
        # attempt 1: backed off, still offered later
        r = _Row("inv-1", attempts=1, next_attempt_at=now + _dt.timedelta(minutes=10))
        assert _skips(r) is True
        # backoff elapsed: offered again
        r = _Row("inv-1", attempts=1, next_attempt_at=now - _dt.timedelta(minutes=1))
        assert _skips(r) is False
        # terminal: never offered again
        r = _Row("inv-1", attempts=PW.PREWARM_MAX_ATTEMPTS, next_attempt_at=None)
        assert _skips(r) is True

    def test_an_already_warmed_invoice_is_never_offered(self):
        """Fails if the warmed check goes — every tick would re-pay for
        invoices that are already cached."""
        assert _skips(_Row("inv-1", warmed_at=PW._now())) is True

    def test_max_attempts_is_small(self):
        """Fails if someone raises retries into storm territory. These are
        paid LLM calls against documents most likely to keep failing."""
        assert PW.PREWARM_MAX_ATTEMPTS <= 3


class TestItCannotDoAnythingButExtract:
    def test_warm_one_touches_no_write_path(self, monkeypatch):
        """Fails if the background path ever gains the power to write to
        Loaded, create a draft, or start a sensei study.

        The draft one matters most: mcp/app_tools._receive_invoice uses "an
        open received_invoice draft exists" as proof a model was shown the
        invoice. Pre-minting drafts would make that check vacuous venue-wide.
        """
        import app.services.invoice_prewarm as mod

        src = __import__("inspect").getsource(mod)
        for forbidden in (
            "do_receive",
            "ensure_draft",
            "_squash_review_into_drafts",
            "autostudy_if_spec_less",
            "apply_open_gates",
            "auto_accept_all",
            "WorkingDocument",
            "review_invoice(",
        ):
            assert forbidden not in src, f"prewarm must not reference {forbidden}"

    def test_it_is_not_parallel(self, monkeypatch):
        """Fails if someone 'optimises' the tick with a thread pool. Nobody is
        waiting on a tick, so trading memory and DB connections for latency is
        pure downside — and that trade is the shape of the Aug OOM aborts."""
        import app.services.invoice_prewarm as mod

        src = __import__("inspect").getsource(mod)
        assert "ThreadPoolExecutor" not in src
        assert "extract_invoice_copies_parallel" not in src


class TestSingleFlight:
    def test_a_second_tick_does_nothing_while_one_holds_the_lock(self, monkeypatch):
        """Fails if the advisory lock goes: four workers times three instances
        would each start a tick and double-bill the same invoices."""
        monkeypatch.setattr(PW, "warm_one", lambda v, i: pytest.fail("must not run"))
        out = _run_tick(monkeypatch, lock_granted=False)
        assert out["locked"] is True
        assert out["warmed"] == 0


# --- harness -----------------------------------------------------------------


def _skips(row) -> bool:
    """Run the real _skip_ids predicate over one stand-in row."""

    class _Q:
        def filter(self, *a):
            return self

        def all(self):
            return [row]

    class _Db:
        def query(self, *a):
            return _Q()

    return row.invoice_id in PW._skip_ids(_Db(), "v-1")


def _run_tick(monkeypatch, *, lock_granted: bool = True) -> dict:
    """Drive prewarm_extractions with the DB boundary stubbed out."""

    class _Result:
        def scalar(self):
            return lock_granted

    class _Sess:
        def execute(self, *a, **k):
            return _Result()

        def commit(self):
            pass

        def close(self):
            pass

        def query(self, *a):
            raise AssertionError("candidates are stubbed in these tests")

    monkeypatch.setattr(PW, "SessionLocal", _Sess, raising=False)
    monkeypatch.setattr(PW, "_ConfigSessionLocal", _Sess, raising=False)
    import app.db.engine as engine

    monkeypatch.setattr(engine, "SessionLocal", _Sess)
    monkeypatch.setattr(engine, "_ConfigSessionLocal", _Sess)
    return PW.prewarm_extractions()


class TestThePrewarmEndpoint:
    """Fail-closed auth, mirroring TestRunDueEndpoint.

    This endpoint spends money on LLM calls, so an unauthenticated caller must
    never reach it — including when the secret is unset, which is exactly when
    a naive check reads "no secret required".
    """

    def test_rejects_missing_secret(self, client, monkeypatch):
        from app.config import settings

        monkeypatch.setattr(settings, "SCHEDULER_SECRET", "s3cret")
        assert client.post("/internal/prewarm-reviews").status_code == 403

    def test_rejects_wrong_secret(self, client, monkeypatch):
        from app.config import settings

        monkeypatch.setattr(settings, "SCHEDULER_SECRET", "s3cret")
        resp = client.post(
            "/internal/prewarm-reviews", headers={"X-Scheduler-Secret": "nope"}
        )
        assert resp.status_code == 403

    def test_rejects_when_no_secret_configured(self, client, monkeypatch):
        from app.config import settings

        monkeypatch.setattr(settings, "SCHEDULER_SECRET", "")
        resp = client.post(
            "/internal/prewarm-reviews", headers={"X-Scheduler-Secret": ""}
        )
        assert resp.status_code == 403

    def test_accepts_correct_secret(self, client, monkeypatch):
        from unittest.mock import patch

        from app.config import settings

        monkeypatch.setattr(settings, "SCHEDULER_SECRET", "s3cret")
        with patch(
            "app.services.invoice_prewarm.prewarm_extractions",
            return_value={"locked": False, "warmed": 0, "failed": 0},
        ):
            resp = client.post(
                "/internal/prewarm-reviews", headers={"X-Scheduler-Secret": "s3cret"}
            )
        assert resp.status_code == 200
        assert resp.json()["warmed"] == 0
