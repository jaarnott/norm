"""Drafts in Norm, handed to Gmail one owner at a time
(services/functions/mailbox/drafts.py):

* versioned edits; sending through the outbox marks the draft sent;
* "send to Gmail" creates the draft on the thread and locks the Norm copy
  (a second hand-over can't make a second Gmail draft; a failed one hands
  the draft back);
* "bring back" takes Gmail's edits and ownership back and deletes the Gmail
  copy;
* the sync follows a handed-over draft: sent from Gmail → sent and linked
  to the message (a proposal draft's link then makes the enquiry quoted);
  deleted in Gmail → unlocked; a hand-over that died half-way → unlocked;
* other organisations' drafts don't exist.

Versioned edits (a stale version is a 409) and shadow mode refusing draft
writes are held through HTTP, in test_functions_mailbox_inbox.py
(``test_drafts_over_http_including_the_hand_over``, ``TestGates``) — the
router passes straight through to this service.
"""

from __future__ import annotations

import base64
import datetime as dt

import pytest

from app.db.functions_models import FnEmailDraft, FnProposal, FnProposalEvent
from app.services.functions import status as status_svc
from app.services.functions.errors import Conflict, Invalid, NotFound
from app.services.functions.mailbox import drafts, sync
from tests.functions_mailbox_fakes import (
    MAILBOX,
    FakeGmail,
    install,
    mailbox_world,
    other_mailbox,
    sessions,
)
from tests.functions_world import add_contact, add_enquiry

UTC = dt.timezone.utc
CUSTOMER = "casey@clientco.nz"


@pytest.fixture()
def fake(monkeypatch):
    fake = FakeGmail()
    install(monkeypatch, fake)
    return fake


def _setup(db, fake):
    w = mailbox_world(db)
    fake.add_message(
        frm=f"Casey <{CUSTOMER}>",
        to=[MAILBOX],
        subject="Birthday drinks",
        thread_id="t-1",
        message_id="<casey@clientco.nz>",
    )
    return w


def _draft(db, w, **kw):
    args = dict(
        account_id=w.account.id,
        gmail_thread_id="t-1",
        to=[CUSTOMER],
        subject="Re: Birthday drinks",
        body_html="<p>We'd love to host you.</p>",
    )
    args.update(kw)
    d = drafts.create_draft(db, w.ctx(), **args)
    db.flush()
    return d


def _tick(db, w):
    out = sync.sync_account(w.account.id, session_factory=sessions(db))
    db.expire_all()
    return out


def _reload(db, d) -> FnEmailDraft:
    db.expire_all()
    return db.query(FnEmailDraft).filter_by(id=d.id).one()


class TestNormDrafts:
    def test_bodies_are_cleaned_on_save(self, db_session, fake):
        w = _setup(db_session, fake)
        d = _draft(
            db_session, w, body_html='<p onclick="x()">Hi<script>bad()</script></p>'
        )
        assert "onclick" not in d.body_html and "script" not in d.body_html

    def test_sending_marks_it_sent_and_locks_it(self, db_session, fake):
        w = _setup(db_session, fake)
        d = _draft(db_session, w)
        result = drafts.send_draft(db_session, w.ctx(), d.id, idempotency_key="d-1")
        assert result.status == "sent" and fake.sent[-1]["threadId"] == "t-1"
        d = _reload(db_session, d)
        assert d.status == "sent" and d.sent_gmail_message_id == result.gmail_message_id
        with pytest.raises(Conflict):
            drafts.update_draft(db_session, w.ctx(), d.id, subject="late")

    def test_another_organisations_draft_does_not_exist(self, db_session, fake):
        w = _setup(db_session, fake)
        rival = other_mailbox(db_session, w)
        theirs = FnEmailDraft(
            organization_id=w.other_org.id, account_id=rival.account.id, subject="x"
        )
        db_session.add(theirs)
        db_session.flush()
        with pytest.raises(NotFound):
            drafts.get_draft(db_session, w.ctx(), theirs.id)
        with pytest.raises(NotFound):
            drafts.send_to_gmail(db_session, w.ctx(), theirs.id)


class TestHandOver:
    def test_send_to_gmail_creates_the_draft_on_the_thread_and_locks_it(
        self, db_session, fake
    ):
        w = _setup(db_session, fake)
        d = _draft(db_session, w)
        handed = drafts.send_to_gmail(
            db_session, w.ctx(), d.id, expected_version=d.version
        )
        assert handed.owner == "gmail" and handed.gmail_draft_id in fake.drafts
        gmail_msg = fake.messages[fake.drafts[handed.gmail_draft_id]]
        assert gmail_msg["threadId"] == "t-1" and "DRAFT" in gmail_msg["labelIds"]
        headers = {h["name"]: h["value"] for h in gmail_msg["payload"]["headers"]}
        assert headers.get("In-Reply-To") == "<casey@clientco.nz>"
        with pytest.raises(Conflict):
            drafts.update_draft(db_session, w.ctx(), d.id, subject="mine now?")
        with pytest.raises(Conflict):
            drafts.send_to_gmail(db_session, w.ctx(), d.id)
        assert len(fake.drafts) == 1
        with pytest.raises(Conflict):
            drafts.send_draft(db_session, w.ctx(), d.id, idempotency_key="nope")

    def test_a_failed_hand_over_gives_the_draft_back(self, db_session, fake):
        w = _setup(db_session, fake)
        d = _draft(db_session, w)
        fake.fail["POST /drafts"] = [500]
        with pytest.raises(Exception):
            drafts.send_to_gmail(db_session, w.ctx(), d.id)
        d = _reload(db_session, d)
        assert (
            d.owner == "norm"
            and d.gmail_draft_id is None
            and d.handed_to_gmail_at is None
        )

    def test_bring_back_takes_gmails_edits_and_deletes_the_gmail_copy(
        self, db_session, fake
    ):
        w = _setup(db_session, fake)
        d = _draft(db_session, w)
        handed = drafts.send_to_gmail(db_session, w.ctx(), d.id)
        did = handed.gmail_draft_id
        msg = fake.messages[fake.drafts[did]]
        edited = (
            "<div>Edited in Gmail: we can do the 14th.</div>"
            '<br><div class="norm-signature">Brew Bar</div>'
            '<div class="gmail_quote">old stuff</div>'
        )
        for part in msg["payload"]["parts"]:
            if part["mimeType"] == "text/html":
                part["body"]["data"] = base64.urlsafe_b64encode(
                    edited.encode()
                ).decode()
        back = drafts.bring_back(db_session, w.ctx(), d.id)
        assert back.owner == "norm" and back.gmail_draft_id is None
        assert "Edited in Gmail" in back.body_html
        assert (
            "norm-signature" not in back.body_html and "old stuff" not in back.body_html
        )
        assert back.to_emails == [CUSTOMER]
        assert did not in fake.drafts

    def test_discarding_a_handed_over_draft_deletes_it_in_gmail(self, db_session, fake):
        w = _setup(db_session, fake)
        d = _draft(db_session, w)
        handed = drafts.send_to_gmail(db_session, w.ctx(), d.id)
        out = drafts.discard_draft(db_session, w.ctx(), d.id)
        assert out.status == "discarded" and handed.gmail_draft_id not in fake.drafts


class TestSyncFollowsHandedOverDrafts:
    def test_sent_from_gmail_is_recorded(self, db_session, fake):
        w = _setup(db_session, fake)
        d = _draft(db_session, w)
        handed = drafts.send_to_gmail(db_session, w.ctx(), d.id)
        sent_id = fake.send_draft_from_gmail(handed.gmail_draft_id)
        _tick(db_session, w)
        d = _reload(db_session, d)
        assert d.status == "sent" and d.sent_gmail_message_id == sent_id

    def test_deleted_in_gmail_unlocks_the_norm_copy(self, db_session, fake):
        w = _setup(db_session, fake)
        d = _draft(db_session, w)
        handed = drafts.send_to_gmail(db_session, w.ctx(), d.id)
        mid = fake.drafts.pop(handed.gmail_draft_id)
        fake.delete_message(mid)
        _tick(db_session, w)
        d = _reload(db_session, d)
        assert (d.status, d.owner, d.gmail_draft_id) == ("open", "norm", None)

    def test_a_hand_over_that_died_half_way_is_unlocked(self, db_session, fake):
        w = _setup(db_session, fake)
        d = _draft(db_session, w)
        d.owner = "gmail"
        d.handed_to_gmail_at = dt.datetime.now(UTC) - dt.timedelta(minutes=30)
        db_session.flush()
        from app.services.functions.mailbox import gmail_client
        from app.services.functions.mailbox.mirror import AccountRef

        acct = AccountRef.of(w.account)
        with gmail_client.client_for(acct, writable=True) as client:
            n = drafts.reconcile_handovers(
                acct, client, sessions(db_session), dt.datetime.now(UTC)
            )
        assert n == 1
        assert _reload(db_session, d).owner == "norm"

    def test_a_proposal_draft_sent_from_gmail_makes_the_enquiry_quoted(
        self, db_session, fake
    ):
        w = _setup(db_session, fake)
        contact = add_contact(db_session, w.org.id, email=CUSTOMER)
        enquiry = add_enquiry(db_session, w.org.id, contact.id, w.venue.id)
        db_session.add(
            FnProposal(organization_id=w.org.id, enquiry_id=enquiry.id, html="<p>P</p>")
        )
        db_session.flush()
        d = _draft(
            db_session,
            w,
            kind="proposal",
            enquiry_id=enquiry.id,
            body_html="<p>Here's your proposal: {{quote_link}}</p>",
        )
        handed = drafts.send_to_gmail(db_session, w.ctx(), d.id)
        assert "/q/" in handed.body_html
        fake.send_draft_from_gmail(handed.gmail_draft_id)
        _tick(db_session, w)
        fact = db_session.query(FnProposalEvent).filter_by(enquiry_id=enquiry.id).one()
        assert (fact.kind, fact.source) == ("sent", "gmail")
        assert fact.proposal_version_id is not None
        stage, _ = status_svc.enquiry_status(db_session, w.org.id, enquiry.id, None)
        assert stage == "quoted"
        assert _reload(db_session, d).status == "sent"


class TestDraftModes:
    """A draft's mode decides how it goes out: reply / reply_all on its
    thread, new as a new conversation, forward as a new conversation that
    carries the thread's message."""

    def test_the_default_mode_is_what_the_draft_holds_and_must_fit(
        self, db_session, fake
    ):
        """Without a mode, a draft on a thread is a reply and one without is
        new; a mode that doesn't fit the conversation is refused."""
        w = _setup(db_session, fake)
        reply = _draft(db_session, w)
        new = _draft(db_session, w, gmail_thread_id=None, subject="Hello")
        assert drafts.draft_dict(reply)["mode"] == "reply"
        assert drafts.draft_dict(new)["mode"] == "new"
        with pytest.raises(Invalid):
            _draft(db_session, w, mode="new")  # has a thread
        with pytest.raises(Invalid):
            _draft(db_session, w, gmail_thread_id=None, mode="reply")
        with pytest.raises(Invalid):
            _draft(db_session, w, mode="sideways")

    def test_a_reply_all_draft_goes_on_its_thread_with_its_copies(
        self, db_session, fake
    ):
        w = _setup(db_session, fake)
        d = _draft(db_session, w, mode="reply_all", cc=["partner@clientco.nz"])
        assert drafts.draft_dict(d)["mode"] == "reply_all"
        drafts.send_draft(db_session, w.ctx(), d.id, idempotency_key="ra-1")
        assert fake.sent[-1]["threadId"] == "t-1"
        assert fake.sent_message()["Cc"] == "partner@clientco.nz"

    def test_a_forward_draft_forwards_and_survives_a_hand_over(self, db_session, fake):
        w = _setup(db_session, fake)
        d = _draft(
            db_session,
            w,
            mode="forward",
            to=["boss@cookbros.co.nz"],
            subject=None,
            body_html="FYI — they want $3,000 worth",  # forwards aren't price-checked
        )
        handed = drafts.send_to_gmail(db_session, w.ctx(), d.id)
        gmail_msg = fake.messages[fake.drafts[handed.gmail_draft_id]]
        assert gmail_msg["threadId"] != "t-1"  # a new conversation
        back = drafts.bring_back(db_session, w.ctx(), d.id)
        assert back.gmail_thread_id == "t-1" and drafts.mode_of(back) == "forward"
        result = drafts.send_draft(db_session, w.ctx(), d.id, idempotency_key="fwd-d")
        assert result.status == "sent" and fake.sent[-1]["threadId"] is None
        msg = fake.sent_message()
        assert msg["Subject"].startswith("Fwd:")
        assert "Forwarded message" in msg.get_body(("html",)).get_content()
