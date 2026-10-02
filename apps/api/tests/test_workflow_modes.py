"""Workflow run-mode storage, tools, and API."""

from app.agents.internal_tools import _get_workflow_mode, _set_workflow_mode
from app.services.workflow_modes import MODE_IDS, WORKFLOW_KEYS, user_mode


class FakeUser:
    def __init__(self, modes=None):
        self.id = "u1"
        self.workflow_modes = modes


class FakeThread:
    id = "t1"
    user_id = "u1"


class FakeQuery:
    def __init__(self, result):
        self._r = result

    def filter(self, *a, **k):
        return self

    def first(self):
        return self._r


class FakeDB:
    def __init__(self, thread, user):
        self._thread, self._user = thread, user
        self.flushed = False

    def query(self, model):
        from app.db.models import Thread

        return FakeQuery(self._thread if model is Thread else self._user)

    def flush(self):
        self.flushed = True


def test_catalog_shapes():
    assert "review_and_receive_invoices" in WORKFLOW_KEYS
    assert MODE_IDS == {"approve_all", "approve_fixes", "autopilot"}


def test_user_mode_reads_and_validates():
    assert (
        user_mode(
            FakeUser({"review_and_receive_invoices": "autopilot"}),
            "review_and_receive_invoices",
        )
        == "autopilot"
    )
    assert user_mode(FakeUser(None), "review_and_receive_invoices") is None
    assert (
        user_mode(
            FakeUser({"review_and_receive_invoices": "bogus"}),
            "review_and_receive_invoices",
        )
        is None
    )


def test_get_workflow_mode_unset():
    user = FakeUser(None)
    db = FakeDB(FakeThread(), user)
    out = _get_workflow_mode({"workflow": "review_and_receive_invoices"}, db, "t1")
    assert out["success"] and out["data"]["mode"] == "unset"


def test_set_workflow_mode_persists():
    from app.db.models import User

    # A real (transient) mapped instance so flag_modified works.
    user = User(id="u1", email="u@x.co", hashed_password="x", full_name="U")
    db = FakeDB(FakeThread(), user)
    out = _set_workflow_mode(
        {"workflow": "reconcile_received_invoices", "mode": "autopilot"}, db, "t1"
    )
    assert out["success"]
    assert user.workflow_modes == {"reconcile_received_invoices": "autopilot"}
    assert db.flushed
    # and it reads back
    got = _get_workflow_mode({"workflow": "reconcile_received_invoices"}, db, "t1")
    assert got["data"]["mode"] == "autopilot"


def test_receiving_is_the_venues_setting_not_a_personal_one():
    """Nothing reads a personal receiving mode — setting one let the model
    confirm a change that never took effect (consolidator review, 1 Oct)."""
    from app.db.models import User

    user = User(id="u1", email="u@x.co", hashed_password="x", full_name="U")
    db = FakeDB(FakeThread(), user)
    out = _set_workflow_mode(
        {"workflow": "review_and_receive_invoices", "mode": "autopilot"}, db, "t1"
    )
    assert not out["success"]
    assert "Settings → Preferences → Receiving invoices" in out["error"]
    assert not user.workflow_modes


def test_set_rejects_bad_values():
    db = FakeDB(FakeThread(), FakeUser(None))
    assert not _set_workflow_mode({"workflow": "nope", "mode": "autopilot"}, db, "t1")[
        "success"
    ]
    assert not _set_workflow_mode(
        {"workflow": "review_and_receive_invoices", "mode": "nope"}, db, "t1"
    )["success"]


class TestAPassedModeOnlyLowers:
    """A run mode in the tool call is the model's say-so: it may lower the
    resolved mode, never raise it (consolidator review, 1 Oct 2026 — passed as
    an argument it used to beat the user's approve_all outright)."""

    ECHO = {
        "action": "reconcile_received_invoices",
        "function_code": "def run(params, call_api, log):\n    return {'mode': params.get('mode')}\n",
    }

    def _thread(self, db_session, modes):
        from app.db.models import Thread
        from tests.conftest import _make_user

        user = _make_user(db_session)
        user.workflow_modes = modes
        thread = Thread(id="t-modes", session_id="s", user_id=user.id, status="open")
        db_session.add(thread)
        db_session.flush()
        return thread.id

    def _run(self, db_session, thread_id, **params):
        from app.agents.internal_tools import execute_consolidator

        out = execute_consolidator(dict(self.ECHO), params, db_session, thread_id)
        data = out.get("data") if isinstance(out.get("data"), dict) else out
        return data.get("mode")

    def test_autopilot_in_the_call_cannot_raise_approve_all(self, db_session):
        tid = self._thread(db_session, {"reconcile_received_invoices": "approve_all"})
        assert self._run(db_session, tid, mode="autopilot") == "approve_all"

    def test_a_safer_mode_in_the_call_still_lowers(self, db_session):
        tid = self._thread(db_session, {"reconcile_received_invoices": "autopilot"})
        assert self._run(db_session, tid, mode="approve_all") == "approve_all"
        assert self._run(db_session, tid) == "autopilot"

    def test_unset_stays_unset_whatever_the_call_says(self, db_session):
        tid = self._thread(db_session, {})
        assert self._run(db_session, tid, mode="autopilot") == "unset"

    def test_without_a_thread_a_passed_mode_does_nothing(self, db_session):
        assert self._run(db_session, None, mode="autopilot") == "unset"
