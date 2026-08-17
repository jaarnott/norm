"""Run one dojo sensei analysis, out of process.

    python -m app.jobs.analyse_sample <sample_id> [--feedback "..."]

The analysis is a 1-2 minute run of the strongest model that also re-extracts
every sibling sample of the spec to check for regressions. Inside the web
process that is a bad citizen twice over: it holds several invoice PDFs and
their base64 payloads at once (it aborted the production container on 12 and
13 Aug 2026, which killed the daemon thread and left the sample showing
"sensei analysing…" until a 15-minute staleness rule noticed), and the
blocking form occupies a gunicorn worker for minutes against Cloud Run's
300-second request ceiling.

Out here a crash is a FAILED execution you can see and re-run, and the memory
belongs to the job rather than to everyone's requests.

Exits non-zero on failure so the execution is recorded as failed — the sample
also records its own failure, but an execution that reports success while the
analysis died would be the same invisible-failure trap all over again.
"""

from __future__ import annotations

import argparse
import logging
import sys

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
logger = logging.getLogger("app.jobs.analyse_sample")


def load_system_secrets() -> None:
    """Pull the real secrets out of the config DB, as FastAPI's startup does.

    NOT optional here. The live ANTHROPIC_API_KEY of record lives in the config
    DB's ``system_secrets`` table and is edited through the Settings UI; the
    Secret Manager copy injected as an env var can be stale or empty. A job
    never boots the FastAPI app, so it never runs that startup hook — without
    this it would fail on an API key that the web process resolves correctly,
    which is a maddening thing to debug.
    """
    from app.config import settings
    from app.db.engine import SessionLocal, _ConfigSessionLocal
    from app.db.models import SystemSecret

    loadable = {
        "ANTHROPIC_API_KEY",
        "JWT_SECRET",
        "RESEND_API_KEY",
    }
    factory = _ConfigSessionLocal or SessionLocal
    db = factory()
    try:
        loaded = 0
        for secret in db.query(SystemSecret).all():
            if secret.key in loadable and secret.value:
                setattr(settings, secret.key, secret.value)
                loaded += 1
        logger.info("loaded %d system secrets from the config DB", loaded)
    finally:
        db.close()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="app.jobs.analyse_sample")
    parser.add_argument("sample_id", help="SupplierSpecSample id to analyse")
    parser.add_argument(
        "--feedback",
        default=None,
        help="an admin's reply to the existing proposal thread",
    )
    args = parser.parse_args(argv)

    load_system_secrets()

    import os as _os

    from app.db.engine import SessionLocal, _ConfigSessionLocal
    from app.services import sensei_runner, spec_dojo

    # Claim the queued run before spending anything. No claim = nothing to do
    # (another execution finished it, or a live one holds it) — exit clean so
    # a duplicate dispatch is harmless.
    claimed_by = _os.environ.get("CLOUD_RUN_EXECUTION", sensei_runner.worker_identity())
    entry = sensei_runner.claim(args.sample_id, claimed_by)
    if entry is None:
        logger.info(
            "sample %s has no claimable run (finished or live elsewhere) — done",
            args.sample_id,
        )
        return 0
    feedback = args.feedback or entry.get("feedback")

    db, config_db = SessionLocal(), _ConfigSessionLocal()
    try:
        logger.info("analysing sample %s (as %s)", args.sample_id, claimed_by)
        result = spec_dojo.analyse_sample(
            db, config_db, args.sample_id, feedback=feedback
        )
        # call_llm only add()+flush()es its llm_calls rows — inside the web
        # process the request lifecycle commits them, but out here nobody
        # does, and close() rolls them back. Without this commit every
        # job-run analysis burns Opus tokens invisibly (Aug 2026: zero
        # dojo_analysis rows recorded after the move to the job).
        db.commit()
        status = (result or {}).get("status")
        logger.info("sample %s finished: %s", args.sample_id, status)
        # analyse_sample catches its own errors and STORES status="failed"
        # rather than raising, so a zero exit here would report a healthy
        # execution for a failed analysis.
        return 0 if status in ("ready", "not_green") else 1
    except Exception:
        logger.exception("analysis of %s failed", args.sample_id)
        return 1
    finally:
        db.close()
        config_db.close()


if __name__ == "__main__":  # pragma: no cover — exercised as a subprocess
    sys.exit(main())
