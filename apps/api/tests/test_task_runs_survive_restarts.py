"""A scheduled run survives its server being stopped (20 Oct 2026).

The scheduler claimed an hourly invoice task — moving its next run on — and
ran it on a bare thread of whichever server took the request. Cloud Run
stopped that server ten seconds later (ordinary scale-in): the run vanished,
no record, no error, and the next run was already an hour away. A claimed run
is now a durable job, picked up by a live worker and, if its server dies, by
another.
"""

import uuid
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy.orm import Session

from app.db.models import AutomatedTask, AutomatedTaskRun, BackgroundJob
from app.services import background_jobs as bj
from app.services import task_scheduler as ts
from tests.org_helpers import make_user


@pytest.fixture
def sessions(db_session, monkeypatch):
    """Every session the job and task code opens joins the test's transaction."""

    def factory():
        return Session(bind=db_session.get_bind())

    monkeypatch.setattr(bj, "_session", factory)
    monkeypatch.setattr("app.db.engine.SessionLocal", factory)
    return factory


@pytest.fixture
def due_task(db_session):
    user = make_user(db_session)
    t = AutomatedTask(
        id=str(uuid.uuid4()),
        title="Hourly receive",
        agent_slug="procurement",
        prompt="receive",
        schedule_type="hourly",
        schedule_config={"interval_minutes": 60},
        status="active",
        created_by=user.id,
        next_run_at=datetime.now(timezone.utc) - timedelta(minutes=1),
    )
    db_session.add(t)
    db_session.commit()
    return t


def _jobs(db_session):
    return (
        db_session.query(BackgroundJob)
        .filter(BackgroundJob.kind == ts.TASK_RUN_JOB)
        .all()
    )


class TestAClaimedRunIsAJob:
    def test_the_scheduler_queues_a_job_instead_of_starting_a_thread(
        self, db_session, sessions, due_task, monkeypatch
    ):
        started = []
        monkeypatch.setattr(ts.threading, "Thread", lambda *a, **k: started.append(k))
        out = ts.run_due_tasks(db=db_session)
        assert out["task_ids"] == [due_task.id]
        assert started == []  # no bare thread
        (job,) = _jobs(db_session)
        assert job.status == "queued" and job.params["task_id"] == due_task.id

    def test_one_task_never_has_two_runs_queued(self, db_session, sessions, due_task):
        ts._enqueue_runs([due_task.id], db=db_session)
        ts._enqueue_runs([due_task.id], db=db_session)
        assert len(_jobs(db_session)) == 1

    def test_a_worker_runs_it(self, db_session, sessions, due_task, monkeypatch):
        ran = []
        monkeypatch.setattr(
            ts,
            "execute_task_now",
            lambda task_id, **k: (
                ran.append(task_id) or {"success": True, "status": "success"}
            ),
        )
        ts._enqueue_runs([due_task.id], db=db_session)
        assert bj.worker_tick("worker-1") is True
        (job,) = _jobs(db_session)
        db_session.refresh(job)
        assert ran == [due_task.id]
        assert job.status == "done" and job.result["status"] == "success"


class TestAfterAServerDies:
    def test_the_run_it_left_behind_is_closed_and_the_task_runs_again(
        self, db_session, sessions, due_task, monkeypatch
    ):
        ts._enqueue_runs([due_task.id], db=db_session)
        (job,) = _jobs(db_session)
        # The first attempt started, wrote a run record, then its server died.
        job.status = "running"
        job.claimed_by = "dead-server"
        job.progress = {"started": True}
        job.heartbeat_at = datetime.now(timezone.utc) - timedelta(
            seconds=bj.HEARTBEAT_STALE_SECONDS + 60
        )
        orphan = AutomatedTaskRun(
            automated_task_id=due_task.id, status="running", mode="live"
        )
        db_session.add(orphan)
        db_session.commit()

        ran = []
        monkeypatch.setattr(
            ts,
            "execute_task_now",
            lambda task_id, **k: (
                ran.append(task_id) or {"success": True, "status": "success"}
            ),
        )
        assert bj.worker_tick("worker-2") is True
        db_session.refresh(orphan)
        db_session.refresh(job)
        assert orphan.status == "error"
        assert "interrupted" in orphan.error_message
        assert ran == [due_task.id]
        assert job.status == "done" and job.claimed_by == "worker-2"
