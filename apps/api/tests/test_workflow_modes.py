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
        {"workflow": "review_and_receive_invoices", "mode": "autopilot"}, db, "t1"
    )
    assert out["success"]
    assert user.workflow_modes == {"review_and_receive_invoices": "autopilot"}
    assert db.flushed
    # and it reads back
    got = _get_workflow_mode({"workflow": "review_and_receive_invoices"}, db, "t1")
    assert got["data"]["mode"] == "autopilot"


def test_set_rejects_bad_values():
    db = FakeDB(FakeThread(), FakeUser(None))
    assert not _set_workflow_mode({"workflow": "nope", "mode": "autopilot"}, db, "t1")[
        "success"
    ]
    assert not _set_workflow_mode(
        {"workflow": "review_and_receive_invoices", "mode": "nope"}, db, "t1"
    )["success"]
