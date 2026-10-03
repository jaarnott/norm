"""Each person decides what Norm may do without asking them.

Until Oct 2026 the same question had two homes and two answers: a personal
"run mode" per invoice workflow (``users.workflow_modes``), and — for receiving
only — a per-venue ladder (``venues.invoice_autopilot``) that a person's own
choice could not lower. Every other write had no setting at all. Now one store,
``users.approval_preferences`` (services/approvals.py): "always allow" for an
ordinary write, a level and switches for the invoice tools. Anyone sets their
own — in Settings, from an approval card, or by asking Norm (which asks first).
"""

import importlib.util
import pathlib
import uuid

import pytest

from app.agents import internal_tools as IT
from app.agents import tool_loop as TL
from app.auth.security import create_access_token
from app.db.config_models import ConnectionSpec, MarketplaceApp
from app.db.models import Approval, AutomatedTask, Thread
from app.services import approvals
from tests.conftest import _make_thread, _make_user
from tests.test_approval_previews import _card_of, _price_previewer, _turn

STOCK = {
    "action": "manage_stock_item",
    "method": "PUT",
    "effect": "write",
    "approval": {"default": "ask", "allow_auto": True, "label": "update stock items"},
}
LOCKED = {
    "action": "set_approval_preference",
    "method": "POST",
    "effect": "write",
    "approval": {
        "default": "ask",
        "allow_auto": False,
        "label": "change what Norm may do without asking you",
    },
}
AUTO = {
    "action": "remember",
    "method": "POST",
    "effect": "write",
    "approval": {"default": "auto", "allow_auto": False, "label": "remember"},
}
LEVELS = [
    {"id": "approve_all", "label": "Ask me", "writes": False},
    {"id": "approve_fixes", "label": "Receive clean invoices", "writes": True},
    {"id": "autopilot", "label": "Autopilot", "writes": True},
]
RECEIVE = {
    "action": "review_and_receive_invoices",
    "method": "GET",
    "effect": "write",
    "approval": {
        "default": "ask",
        "allow_auto": True,
        "label": "receive supplier invoices",
        "levels": LEVELS,
        "options": [
            {"id": "auto_create_units", "label": "create a unit in Loaded"},
            {
                "id": "auto_strike_phantom_lines",
                "label": "strike lines the copy doesn't bill",
            },
        ],
    },
}


def _person(db, prefs=None):
    user = _make_user(db)
    user.approval_preferences = prefs
    db.flush()
    return user


def _headers(user):
    return {"Authorization": f"Bearer {create_access_token({'sub': user.id})}"}


# --------------------------------------------------------------- migration ----


def _migration():
    path = next(
        pathlib.Path(__file__)
        .resolve()
        .parents[1]
        .glob("alembic/versions/w8x9y0z1a2b3_*.py")
    )
    spec = importlib.util.spec_from_file_location("stage4_migration", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.preferences_for


ON = {
    "mode": "autopilot",
    "auto_strike_phantom_lines": True,
    "auto_delete_duplicates": True,
}


class TestTheMigrationKeepsWhatEachPersonHad:
    """Production, 3 Oct 2026: all six venues on autopilot with the same three
    switches; Felipe had chosen approve_fixes for receiving and the platform
    admin approve_all — both ignored while receiving was the venue's."""

    def test_venues_on_autopilot_carry_over_with_their_switches(self):
        prefs = _migration()({}, [ON, ON], False)
        assert prefs == {
            approvals.RECEIVE_KEY: {
                "level": "autopilot",
                "options": {
                    "auto_delete_duplicates": True,
                    "auto_strike_phantom_lines": True,
                },
            }
        }

    def test_the_lowest_venue_wins_and_a_switch_needs_every_venue(self):
        fixes = {"mode": "approve_fixes", "auto_strike_phantom_lines": True}
        prefs = _migration()({}, [ON, fixes], False)
        assert prefs[approvals.RECEIVE_KEY] == {
            "level": "approve_fixes",
            "options": {"auto_strike_phantom_lines": True},
        }

    def test_a_lower_personal_choice_wins(self):
        felipe = {"review_and_receive_invoices": "approve_fixes"}
        assert _migration()(felipe, [ON], False)[approvals.RECEIVE_KEY]["level"] == (
            "approve_fixes"
        )
        admin = {"review_and_receive_invoices": "approve_all"}
        assert approvals.RECEIVE_KEY not in _migration()(admin, [ON], False)

    def test_reconciling_keeps_the_personal_mode(self):
        dianna = {"reconcile_received_invoices": "approve_fixes"}
        prefs = _migration()(dianna, [], False)
        assert prefs == {approvals.RECONCILE_KEY: {"level": "approve_fixes"}}

    def test_a_task_owner_keeps_their_report_email_running(self):
        """The report email ran unasked until it was labelled a write; a daily
        report must not stop at a card nobody is watching."""
        assert _migration()({}, [], True) == {
            "norm_email.send_report_email": {"always": True}
        }

    def test_nobody_gets_more_than_they_had(self):
        assert _migration()({}, [], False) == {}
        assert (
            _migration()({"reconcile_received_invoices": "nonsense"}, [{}], False) == {}
        )


# -------------------------------------------------------------------- gate ----


class TestTheGate:
    def test_an_always_allowed_write_runs_without_asking(self, db_session):
        mine = _person(db_session, {"loadedhub.manage_stock_item": {"always": True}})
        assert (
            approvals.gate(STOCK, "PUT", "loadedhub", "manage_stock_item", mine)
            == "auto"
        )

    def test_everyone_else_is_still_asked(self, db_session):
        other = _person(db_session)
        assert (
            approvals.gate(STOCK, "PUT", "loadedhub", "manage_stock_item", other)
            == "ask"
        )
        assert (
            approvals.gate(STOCK, "PUT", "loadedhub", "manage_stock_item", None)
            == "ask"
        )

    def test_a_tool_that_must_ask_ignores_a_stored_always(self, db_session):
        """Norm can never raise its own autonomy silently — even a hand-edited
        preference can't switch off the question."""
        sneaky = _person(db_session, {"norm.set_approval_preference": {"always": True}})
        assert (
            approvals.gate(LOCKED, "POST", "norm", "set_approval_preference", sneaky)
            == "ask"
        )


class TestSettingAPreference:
    def test_always_then_ask_is_stored_then_cleared_and_recorded(self, db_session):
        user = _person(db_session)
        key = "loadedhub.manage_stock_item"
        approvals.set_preference(db_session, user, key, STOCK, "always", via="settings")
        assert user.approval_preferences == {key: {"always": True}}
        approvals.set_preference(db_session, user, key, STOCK, "ask", via="card")
        assert user.approval_preferences == {}
        rows = (
            db_session.query(Approval)
            .filter(
                Approval.user_id == user.id,
                Approval.action == "approval_preference_set",
            )
            .all()
        )
        assert len(rows) == 2 and all(r.thread_id is None for r in rows)
        assert any("via card" in r.notes for r in rows)

    @pytest.mark.parametrize(
        "row,value",
        [
            (LOCKED, "always"),  # must always ask
            (AUTO, "ask"),  # nothing to choose
            (RECEIVE, {"level": "turbo"}),
            (
                RECEIVE,
                {"level": "autopilot", "options": {"auto_delete_everything": True}},
            ),
            (STOCK, "sometimes"),
        ],
    )
    def test_what_the_tool_does_not_allow_is_refused(self, db_session, row, value):
        with pytest.raises(ValueError):
            approvals.set_preference(
                db_session, _person(db_session), "k.k", row, value, via="settings"
            )

    def test_a_level_keeps_only_switches_that_are_on(self, db_session):
        user = _person(db_session)
        approvals.set_preference(
            db_session,
            user,
            approvals.RECEIVE_KEY,
            RECEIVE,
            {
                "level": "autopilot",
                "options": {
                    "auto_create_units": True,
                    "auto_strike_phantom_lines": False,
                },
            },
            via="settings",
        )
        settings = approvals.receiving_settings(user)
        assert settings["mode"] == "autopilot"
        assert settings["auto_create_units"] is True
        assert settings["auto_strike_phantom_lines"] is False

    def test_nobody_receives_at_the_safest_level(self):
        settings = approvals.receiving_settings(None)
        assert settings["mode"] == "approve_all"
        assert not any(v is True for v in settings.values())


# ------------------------------------------------------------------ levels ----


class TestTheInvoiceToolsRunAtThePersonsLevel:
    ECHO = "def run(params, call_api, log):\n    return {'mode': params.get('mode')}\n"

    def _run(self, db, thread_id, action, **params):
        out = IT.execute_consolidator(
            {"action": action, "function_code": self.ECHO}, params, db, thread_id
        )
        return out["data"]["mode"]

    def _thread(self, db, prefs, task_config=None):
        user = _person(db, prefs)
        thread = _make_thread(db, user, domain="norm", intent="norm.tool_use")
        if task_config is not None:
            db.add(
                AutomatedTask(
                    title="t",
                    agent_slug="norm",
                    prompt="p",
                    created_by=user.id,
                    conversation_thread_id=thread.id,
                    task_config=task_config,
                )
            )
            db.flush()
        return thread.id

    def test_receiving_follows_the_person_not_the_venue(self, db_session):
        tid = self._thread(
            db_session, {approvals.RECEIVE_KEY: {"level": "approve_fixes"}}
        )
        assert (
            self._run(db_session, tid, "review_and_receive_invoices") == "approve_fixes"
        )

    def test_the_model_can_lower_the_level_never_raise_it(self, db_session):
        tid = self._thread(
            db_session, {approvals.RECONCILE_KEY: {"level": "approve_fixes"}}
        )
        action = "reconcile_received_invoices"
        assert self._run(db_session, tid, action, mode="autopilot") == "approve_fixes"
        assert self._run(db_session, tid, action, mode="approve_all") == "approve_all"

    def test_a_task_setting_lowers_too(self, db_session):
        tid = self._thread(
            db_session,
            {approvals.RECONCILE_KEY: {"level": "autopilot"}},
            task_config={"mode": "approve_fixes"},
        )
        assert (
            self._run(db_session, tid, "reconcile_received_invoices") == "approve_fixes"
        )

    def test_no_choice_and_no_person_both_run_at_ask_me(self, db_session):
        """'Unset' used to mean 'stop and ask the user to pick a mode'. The first
        level writes nothing, so there is never a reason to wait."""
        tid = self._thread(db_session, None)
        assert (
            self._run(db_session, tid, "reconcile_received_invoices") == "approve_all"
        )
        assert self._run(
            db_session, None, "review_and_receive_invoices", mode="autopilot"
        ) == ("approve_all")

    def test_the_review_service_is_handed_the_persons_switches(
        self, db_session, monkeypatch
    ):
        seen = {}
        monkeypatch.setattr(
            "app.services.invoice_review.review_invoices",
            lambda *a, **k: seen.update(k) or {"cards": []},
        )
        monkeypatch.setattr(IT, "_scoped_venue_id", lambda p, d: ("v-1", None))
        tid = self._thread(
            db_session,
            {
                approvals.RECEIVE_KEY: {
                    "level": "autopilot",
                    "options": {"auto_create_units": True},
                }
            },
        )
        IT._review_invoices({"mode": "autopilot"}, db_session, tid)
        assert seen["settings"]["mode"] == "autopilot"
        assert seen["settings"]["auto_create_units"] is True


# -------------------------------------------------------------- the loop ----


def _spec(db, row):
    name = f"t{uuid.uuid4().hex[:10]}"
    db.add(
        ConnectionSpec(
            connector_name=name,
            display_name=name,
            execution_mode="internal",
            auth_type="none",
            tools=[row],
        )
    )
    db.flush()
    return name


class TestTheLoop:
    def _wire(self, db_session, monkeypatch, row):
        conn = _spec(db_session, {**row, "action": "set_price"})
        monkeypatch.setitem(IT._PREVIEWERS, (conn, "set_price"), _price_previewer(34))
        ran = []
        monkeypatch.setitem(
            IT._REGISTRY,
            (conn, "set_price"),
            lambda p, d, t: ran.append(p) or {"success": True, "data": {"ok": True}},
        )
        return conn, ran

    def test_a_write_the_person_always_allows_runs_with_no_card(
        self, db_session, monkeypatch
    ):
        conn, ran = self._wire(db_session, monkeypatch, STOCK)
        user = _person(db_session, {f"{conn}.set_price": {"always": True}})
        thread, seen = _turn(
            db_session, user, monkeypatch, conn, "set_price", [{"price": 36}]
        )

        assert thread.status != approvals.AWAITING
        assert [p["price"] for p in ran] == [36]
        assert "always allows it" in str(seen[1])  # Norm is told, so it says so
        audit = db_session.query(Approval).filter(Approval.thread_id == thread.id).one()
        assert audit.action == "tool_call_always_allowed"

    def test_someone_else_with_the_same_tool_is_still_asked(
        self, db_session, monkeypatch
    ):
        conn, ran = self._wire(db_session, monkeypatch, STOCK)
        _person(db_session, {f"{conn}.set_price": {"always": True}})
        stranger = _person(db_session)
        thread, _ = _turn(
            db_session, stranger, monkeypatch, conn, "set_price", [{"price": 36}]
        )
        assert thread.status == approvals.AWAITING
        assert ran == []


class TestAlwaysAllowFromTheCard:
    def test_ticking_it_saves_the_preference_for_the_approver(
        self, client, db_session, monkeypatch
    ):
        conn, ran = TestTheLoop()._wire(db_session, monkeypatch, STOCK)
        user = _person(db_session)
        thread, _ = _turn(
            db_session, user, monkeypatch, conn, "set_price", [{"price": 36}]
        )
        row = _card_of(db_session, thread)["data"]["tool_calls"][0]
        assert row["approval"] == {
            "key": f"{conn}.set_price",
            "label": "update stock items",
            "allow_auto": True,
        }
        monkeypatch.setattr(
            TL, "_execute_loop", lambda *a, **k: {"status": "completed"}
        )

        r = client.post(
            f"/api/threads/{thread.id}/approve",
            json={"always_allow": [f"{conn}.set_price", "loadedhub.manage_menu"]},
            headers=_headers(user),
        )

        assert r.status_code == 200, r.text
        assert [p["price"] for p in ran] == [36]
        db_session.refresh(user)
        # Only the tool on the card — a card can't switch on anything else.
        assert user.approval_preferences == {f"{conn}.set_price": {"always": True}}


# --------------------------------------------------------- settings api ----


@pytest.fixture()
def catalog(db_session, monkeypatch):
    """A connector with each kind of write, claimed by an App."""
    conn = _spec(db_session, STOCK)
    spec = db_session.query(ConnectionSpec).filter_by(connector_name=conn).one()
    spec.tools = [STOCK, LOCKED, AUTO, RECEIVE]
    db_session.add(
        MarketplaceApp(
            slug=f"app-{conn}",
            name="Stock",
            description="",
            tier="app",
            bundled=True,
            status="active",
            composition={
                "member": "*",
                "tools": [
                    f"{conn}.{r['action']}" for r in (STOCK, LOCKED, AUTO, RECEIVE)
                ],
            },
        )
    )
    db_session.flush()
    monkeypatch.setattr("app.services.entitlements.apps_on", lambda *a, **k: None)
    return conn


class TestTheSettingsPage:
    def test_it_lists_every_write_with_its_kind(self, client, db_session, catalog):
        user = _person(db_session)
        r = client.get("/api/approval-preferences", headers=_headers(user))
        assert r.status_code == 200, r.text
        mine = [t for t in r.json()["tools"] if t["key"].startswith(catalog)]
        assert [(t["label"], t["kind"]) for t in mine] == [
            ("Receive supplier invoices", "levels"),
            ("Update stock items", "ask"),
            ("Change what Norm may do without asking you", "locked"),
        ]
        assert mine[0]["level"] == "approve_all"

    def test_it_saves_only_for_the_caller(self, client, db_session, catalog):
        user, other = _person(db_session), _person(db_session)
        r = client.put(
            "/api/approval-preferences",
            json={"key": f"{catalog}.manage_stock_item", "value": "always"},
            headers=_headers(user),
        )
        assert r.status_code == 200, r.text
        assert r.json()["value"] == "always"
        db_session.refresh(user)
        assert user.approval_preferences == {
            f"{catalog}.manage_stock_item": {"always": True}
        }
        assert not other.approval_preferences

    def test_it_refuses_what_the_tool_does_not_allow(self, client, db_session, catalog):
        user = _person(db_session)
        locked = client.put(
            "/api/approval-preferences",
            json={"key": f"{catalog}.set_approval_preference", "value": "always"},
            headers=_headers(user),
        )
        assert locked.status_code == 400
        unknown = client.put(
            "/api/approval-preferences",
            json={"key": "nope.nothing", "value": "always"},
            headers=_headers(user),
        )
        assert unknown.status_code == 404

    def test_the_old_endpoints_are_gone(self, client, admin_headers):
        assert (
            client.get("/api/workflow-modes", headers=admin_headers).status_code == 404
        )
        assert (
            client.get(
                "/api/venues/x/invoice-autopilot", headers=admin_headers
            ).status_code
            == 404
        )


# ------------------------------------------------------------ chat + prompt ----


@pytest.fixture()
def entries(monkeypatch):
    rows = {
        "loadedhub.manage_stock_item": STOCK,
        "norm.set_approval_preference": LOCKED,
        approvals.RECEIVE_KEY: RECEIVE,
    }
    out = [{"key": k, "row": r, "app": "a"} for k, r in rows.items()]
    monkeypatch.setattr(approvals, "catalog", lambda *a, **k: out)

    class _NoConfigDb:
        def close(self):
            pass

    monkeypatch.setattr("app.db.engine._ConfigSessionLocal", _NoConfigDb)
    return out


class TestAskingNorm:
    def test_the_change_is_previewed_then_saved(self, db_session, entries):
        user = _person(db_session)
        thread = _make_thread(db_session, user, domain="norm", intent="norm.tool_use")
        params = {"tool": "update stock items", "setting": "always"}

        shown = IT._set_approval_preference_preview(params, db_session, thread.id)
        assert shown["preview"]["changes"] == [
            {
                "field": "Setting",
                "before": "asks first",
                "after": "always allowed — no card",
            }
        ]
        out = IT._set_approval_preference(params, db_session, thread.id)
        assert out["success"]
        assert user.approval_preferences == {
            "loadedhub.manage_stock_item": {"always": True}
        }

    def test_a_switch_is_added_to_what_is_already_on(self, db_session, entries):
        user = _person(
            db_session,
            {
                approvals.RECEIVE_KEY: {
                    "level": "autopilot",
                    "options": {"auto_strike_phantom_lines": True},
                }
            },
        )
        thread = _make_thread(db_session, user, domain="norm", intent="norm.tool_use")
        IT._set_approval_preference(
            {"tool": approvals.RECEIVE_KEY, "options": {"auto_create_units": True}},
            db_session,
            thread.id,
        )
        assert user.approval_preferences[approvals.RECEIVE_KEY] == {
            "level": "autopilot",
            "options": {"auto_strike_phantom_lines": True, "auto_create_units": True},
        }

    def test_it_cannot_unlock_itself(self, db_session, entries):
        user = _person(db_session)
        thread = _make_thread(db_session, user, domain="norm", intent="norm.tool_use")
        out = IT._set_approval_preference(
            {"tool": "norm.set_approval_preference", "setting": "always"},
            db_session,
            thread.id,
        )
        assert out["success"] is False and "always asks" in out["error"]


class TestThePromptSaysWhatRunsUnasked:
    def test_levels_and_always_allowed_writes_are_stated(self, db_session, entries):
        from app.agents.prompt_builder import approval_preferences_guidance

        user = _person(
            db_session,
            {
                "loadedhub.manage_stock_item": {"always": True},
                approvals.RECEIVE_KEY: {
                    "level": "autopilot",
                    "options": {"auto_create_units": True},
                },
            },
        )
        text = approval_preferences_guidance(
            {
                "manage_stock_item",
                "review_and_receive_invoices",
                "set_approval_preference",
            },
            db_session,
            user.id,
            None,
        )
        assert "receive supplier invoices" in text
        assert "Autopilot — may also create a unit in Loaded" in text
        assert (
            "update stock items (`loadedhub.manage_stock_item`): **always allowed**"
            in text
        )
        assert "set_approval_preference" in text

    def test_writes_that_ask_need_no_mention(self, db_session, entries):
        from app.agents.prompt_builder import approval_preferences_guidance

        user = _person(db_session)
        text = approval_preferences_guidance(
            {"manage_stock_item"}, db_session, user.id, None
        )
        assert text == ""


def test_a_thread_without_a_person_is_handled(db_session):
    thread = Thread(id=str(uuid.uuid4()), session_id="s", user_id=None, status="open")
    db_session.add(thread)
    db_session.flush()
    assert approvals.receiving_settings_for_thread(db_session, thread.id)["mode"] == (
        "approve_all"
    )
