"""Customer sends from chat: send_email and send_proposal through the real
approval flow — the agent loop suspends on a card, a person approves, the
loop resumes and the outbox sends — against a fake Gmail.

* Nothing goes out without the card; the card shows who, what and the
  warnings, and pins the idempotency key the outbox sends under.
* A draft edited after the card was shown is not sent (the fingerprint).
* A price in a plain email is refused at the card unless the person's own
  reason comes with it; a proposal send shows the options and the money check
  and makes the enquiry quoted.
* Shadow mode, a sent draft, the wrong kind and a call without a card are
  all refused.
"""

from __future__ import annotations

import datetime as dt
import importlib.util
import pathlib
import sys
import uuid
from contextlib import contextmanager

import pytest

from app.agents import functions_tools as FT
from app.agents import internal_tools as IT
from app.agents import tool_loop as TL
from app.db.config_models import ConnectionSpec
from app.db.functions_models import FnEmailDraft, FnOutbox
from app.db.models import ToolCall
from app.services import approvals
from app.services.functions import enquiries as enquiries_svc
from app.services.functions import min_spends as min_spends_svc
from app.services.functions.mailbox import drafts as drafts_svc
from app.services.functions.mailbox import sync
from tests.conftest import _make_thread
from tests.functions_mailbox_fakes import (
    MAILBOX,
    FakeGmail,
    install,
    mailbox_world,
    sessions,
)
from tests.functions_world import add_contact, add_enquiry
from tests.test_tool_loop_unknown_tool import _Block, _Response

UTC = dt.timezone.utc
CUSTOMER = "casey@clientco.nz"
API = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(API))


def _sync_script():
    spec = importlib.util.spec_from_file_location(
        "sync_norm_functions", API / "scripts" / "sync_norm_functions.py"
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture(autouse=True)
def _config_db(monkeypatch, db_session):
    @contextmanager
    def same_session():
        yield db_session

    monkeypatch.setattr(FT, "_config_db", same_session)


@pytest.fixture()
def fake(monkeypatch):
    fake = FakeGmail()
    install(monkeypatch, fake)
    return fake


@pytest.fixture()
def w(db_session, fake):
    """Brew Bar's connected mailbox with Casey's enquiry on a linked thread,
    and the norm_functions spec rows the loop reads effects from."""
    w = mailbox_world(db_session)
    fake.add_message(
        frm=f"Casey Client <{CUSTOMER}>",
        to=[MAILBOX],
        subject="Wedding in March",
        body="Can we book the deck?",
        thread_id="t-casey",
        message_id="<casey@clientco.nz>",
        when=dt.datetime.now(UTC) - dt.timedelta(hours=2),
    )
    assert sync.sync_account(w.account.id, session_factory=sessions(db_session)).get(
        "ok"
    )
    db_session.expire_all()
    contact = add_contact(db_session, w.org.id, email=CUSTOMER)
    w.enquiry = add_enquiry(
        db_session,
        w.org.id,
        contact.id,
        w.venue.id,
        subject="Wedding",
        guests=80,
        booking_type="large_event_exclusive",
    )
    enquiries_svc.link_thread(
        db_session,
        w.ctx(),
        account_id=w.account.id,
        gmail_thread_id="t-casey",
        enquiry_id=w.enquiry.id,
    )
    sync_script = _sync_script()
    db_session.query(ConnectionSpec).filter_by(connector_name="norm_functions").delete()
    db_session.add(
        ConnectionSpec(
            connector_name="norm_functions",
            tools=sync_script.tool_rows(),
            endpoints=[],
            **sync_script.SPEC,
        )
    )
    db_session.flush()
    return w


def _draft(db, w, body="<p>Yes — the deck is free that night.</p>", kind="email"):
    handler = IT.get_handler("norm_functions", "draft_email")
    thread = _make_thread(db, w.staff, domain="functions", intent="functions.tool_use")
    out = handler(
        {"enquiry_id": w.enquiry.id, "kind": kind, "body": body}, db, thread.id
    )
    assert out["success"], out["error"]
    return db.get(FnEmailDraft, out["data"]["draft_id"])


def _turn(db, w, monkeypatch, tool, inputs):
    """One model turn that calls ``tool`` once with ``inputs``."""
    seen = []

    def llm(system_prompt, messages, tools, db, thread_id, call_type):
        seen.append(messages[-1])
        if len(seen) == 1:
            return (
                _Response(
                    "tool_use",
                    [
                        _Block("text", text="Sending it now."),
                        _Block(
                            "tool_use",
                            name=f"norm_functions__{tool}",
                            id=f"toolu_{uuid.uuid4().hex[:8]}",
                            input=inputs,
                        ),
                    ],
                ),
                None,
            )
        return _Response("end_turn", [_Block("text", text="Done.")]), None

    monkeypatch.setattr("app.interpreter.llm_interpreter.call_llm_with_tools", llm)
    thread = _make_thread(
        db,
        w.staff,
        domain="functions",
        intent="functions.tool_use",
        status="in_progress",
    )
    defs = [
        {
            "name": f"norm_functions__{tool}",
            "description": "[POST] test",
            "input_schema": {"type": "object", "properties": {}},
        }
    ]
    TL.run_tool_loop("go", thread, db, "sys", defs, config_db=db)
    calls = db.query(ToolCall).filter(ToolCall.thread_id == thread.id).all()
    return thread, calls, seen


def _approve(db, w, thread):
    approvals.claim(db, thread)
    approvals.decide(db, thread, w.staff, approve=True)
    TL.resume_tool_loop(thread, db, "sys", [], config_db=db)


def _rows(tc) -> dict:
    return {r["field"]: r["after"] for r in tc.preview["changes"]}


class TestSendEmail:
    def test_nothing_goes_until_approved_then_one_email_under_the_cards_key(
        self, db_session, w, fake, monkeypatch
    ):
        draft = _draft(db_session, w)
        thread, (tc,), _ = _turn(
            db_session, w, monkeypatch, "send_email", {"draft_id": draft.id}
        )
        assert thread.status == approvals.AWAITING and fake.sent == []
        assert tc.status == "pending_approval"
        assert _rows(tc)["To"] == CUSTOMER and _rows(tc)["From"] == MAILBOX
        assert _rows(tc)["Sent as"] == "a reply on “Wedding in March”"
        key = tc.input_params["idempotency_key"]
        assert tc.input_params["draft_version"] == draft.version

        _approve(db_session, w, thread)
        assert len(fake.sent) == 1 and fake.sent[0]["threadId"] == "t-casey"
        assert (
            db_session.query(FnOutbox).filter_by(idempotency_key=key).one().status
            == "sent"
        )
        db_session.refresh(draft)
        assert draft.status == "sent"
        assert db_session.get(ToolCall, tc.id).status == "executed"
        assert (
            enquiries_svc.enquiry_state(db_session, w.ctx(), w.enquiry.id)[0]
            == "replied"
        )

        again = IT.get_previewer("norm_functions", "send_email")(
            {"draft_id": draft.id}, db_session, thread.id
        )
        assert "already sent" in again["error"]

    def test_a_draft_edited_after_the_card_is_not_sent(
        self, db_session, w, fake, monkeypatch
    ):
        draft = _draft(db_session, w)
        thread, (tc,), _ = _turn(
            db_session, w, monkeypatch, "send_email", {"draft_id": draft.id}
        )
        drafts_svc.update_draft(
            db_session, w.ctx(), draft.id, body_html="<p>Changed by staff</p>"
        )
        _approve(db_session, w, thread)
        assert fake.sent == []
        tc = db_session.get(ToolCall, tc.id)
        assert tc.status == "failed" and "changed" in (tc.error_message or "")

    def test_a_price_without_a_proposal_needs_the_persons_reason(
        self, db_session, w, fake, monkeypatch
    ):
        draft = _draft(db_session, w, body="<p>The deck is $9,000 minimum spend.</p>")
        thread, (tc,), seen = _turn(
            db_session, w, monkeypatch, "send_email", {"draft_id": draft.id}
        )
        assert thread.status != approvals.AWAITING and tc.status == "failed"
        assert "proposal" in str(seen[1]) and "price_override_reason" in str(seen[1])

        thread, (tc,), _ = _turn(
            db_session,
            w,
            monkeypatch,
            "send_email",
            {
                "draft_id": draft.id,
                "price_override_reason": "Customer asked for the figure by email",
            },
        )
        assert thread.status == approvals.AWAITING
        assert any("reason recorded" in x for x in tc.preview["warnings"])

    def test_a_draft_with_blanks_never_reaches_a_card(self, db_session, w, fake):
        draft = _draft(
            db_session, w, body="<p>Hi {{client_name}}, see you on [DATE].</p>"
        )
        assert "Hi Casey" in draft.body_html
        thread = _make_thread(
            db_session, w.staff, domain="functions", intent="functions.tool_use"
        )
        out = IT.get_previewer("norm_functions", "send_email")(
            {"draft_id": draft.id}, db_session, thread.id
        )
        assert "unfilled blanks: [DATE]" in out["error"]

    def test_without_a_card_it_refuses(self, db_session, w, fake):
        draft = _draft(db_session, w)
        handler = IT.get_handler("norm_functions", "send_email")
        thread = _make_thread(
            db_session, w.staff, domain="functions", intent="functions.tool_use"
        )
        out = handler({"draft_id": draft.id}, db_session, thread.id)
        assert not out["success"] and "approval card" in out["error"]
        assert fake.sent == []

    def test_a_proposal_draft_is_not_an_email(self, db_session, w, fake):
        draft = _draft(
            db_session, w, kind="proposal", body="Here's your proposal: {{quote_link}}"
        )
        thread = _make_thread(
            db_session, w.staff, domain="functions", intent="functions.tool_use"
        )
        out = IT.get_previewer("norm_functions", "send_email")(
            {"draft_id": draft.id}, db_session, thread.id
        )
        assert "send_proposal" in out["error"]


class TestSendProposal:
    def test_the_card_shows_options_and_the_check_and_sending_quotes_it(
        self, db_session, w, fake, monkeypatch
    ):
        deck = min_spends_svc.add_min_spend(
            db_session,
            w.ctx(),
            enquiry_id=w.enquiry.id,
            venue_id=w.venue.id,
            amount_cents=900000,
            area_id=w.deck.id,
            label="The Deck",
            event_date=dt.date.today() + dt.timedelta(days=40),
        )
        build = IT.get_handler("norm_functions", "build_proposal")
        thread = _make_thread(
            db_session, w.staff, domain="functions", intent="functions.tool_use"
        )
        assert build({"enquiry_id": w.enquiry.id}, db_session, thread.id)["success"]
        draft = _draft(
            db_session,
            w,
            kind="proposal",
            body="<p>Here's your proposal: {{quote_link}}</p>",
        )

        thread, (tc,), _ = _turn(
            db_session, w, monkeypatch, "send_proposal", {"draft_id": draft.id}
        )
        assert thread.status == approvals.AWAITING and fake.sent == []
        option = _rows(tc)["Option: The Deck"]
        assert "$9,000 total" in option and "can't confirm until approved" in option
        assert any("isn't approved yet" in x for x in tc.preview["warnings"])

        min_spends_svc.approve_min_spend(db_session, w.ctx(w.approver), deck.id)
        _approve(db_session, w, thread)
        # The approval changed what the customer can do, so the card the
        # person saw no longer matches: not sent, ask again.
        assert fake.sent == []

        thread, (tc,), _ = _turn(
            db_session, w, monkeypatch, "send_proposal", {"draft_id": draft.id}
        )
        assert "can confirm" in _rows(tc)["Option: The Deck"]
        _approve(db_session, w, thread)
        assert len(fake.sent) == 1
        body = fake.sent_message().get_body(("html",)).get_content()
        assert "/q/" in body
        assert (
            enquiries_svc.enquiry_state(db_session, w.ctx(), w.enquiry.id)[0]
            == "quoted"
        )

    def test_no_proposal_no_card(self, db_session, w, fake):
        draft = _draft(db_session, w, kind="proposal", body="Here: {{quote_link}}")
        thread = _make_thread(
            db_session, w.staff, domain="functions", intent="functions.tool_use"
        )
        out = IT.get_previewer("norm_functions", "send_proposal")(
            {"draft_id": draft.id}, db_session, thread.id
        )
        assert "build_proposal" in out["error"]


def test_shadow_mode_never_sends(db_session, fake, monkeypatch):
    w = mailbox_world(db_session, mode="shadow")
    w.org.functions_mode = "live"
    db_session.flush()
    contact = add_contact(db_session, w.org.id, email=CUSTOMER)
    w.enquiry = add_enquiry(
        db_session, w.org.id, contact.id, w.venue.id, subject="Wedding"
    )
    draft = _draft(db_session, w)
    w.org.functions_mode = "shadow"
    db_session.flush()
    thread = _make_thread(
        db_session, w.staff, domain="functions", intent="functions.tool_use"
    )
    out = IT.get_previewer("norm_functions", "send_email")(
        {"draft_id": draft.id}, db_session, thread.id
    )
    assert "shadow" in out["error"]
    assert fake.sent == []
