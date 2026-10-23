"""The onboarding form — every DESIGN-CONTRACT §10 rule, blocking:

no token-less form (404, writes nothing); a spent link refuses a second
submission; the bank and tax step refuses without a valid code; the sixth
wrong code of the day is refused even when right; no public response ever
carries a stored bank account, IRD number or date of birth; the IRD check
digit is validated; a band member's link writes only that member; GST comes
from the registration answer; a submitted or bank-changed performer yields
no pay line and no bill, and a bank change emails the previous address; a
``team_member`` cannot accept; the headers on every response.
"""

from __future__ import annotations

import datetime as dt

import pytest

from app.db.entertainers_models import (
    EntBandMember,
    EntEmailLink,
    EntPerformer,
    EntPublicToken,
)
from app.db.models import EmailLog
from app.services.entertainers import payments, tokens
from app.services.entertainers.onboarding import DECLARATION_WORDING_VERSION
from app.services.finance_core.errors import Forbidden
from tests.entertainers_world import (
    SECRET_VALUES,
    add_performer,
    approved,
    assert_no_sensitive,
    join_link,
    make_world,
    set_code,
    verified,
)

HEADERS = {
    "Referrer-Policy": "no-referrer",
    "X-Robots-Tag": "noindex, nofollow",
    "Cache-Control": "no-store",
}


def full_submission(**over) -> dict:
    body = {
        "name": "Sam Smith",
        "email": "sam@example.test",
        "phone": "021 000",
        "performer_type": "solo",
        "paid_as": "individual",
        "legal_name": "Samuel Smith",
        "gst_registered": False,
        "bank_account": "12-3456-7890138-00",
        "ird_number": "49091850",
        "date_of_birth": "1990-01-02",
        "contractor_confirmations": {
            k: True
            for k in (
                "independent",
                "accept_decline",
                "other_clients",
                "own_equipment",
                "own_tax",
                "advice",
            )
        },
        "declaration": {
            "nz_tax_resident": True,
            "wt_rate_type": "standard",
            "signature": "Samuel Smith",
            "confirmed": True,
        },
    }
    body.update(over)
    return body


@pytest.fixture
def w(db_session):
    return make_world(db_session)


@pytest.fixture
def invited(w):
    performer = add_performer(
        w.db, w.org, status="invited", lane=None, bank=None, ird=None, declaration=False
    )
    performer.date_of_birth = None
    performer.legal_name = None
    w.db.flush()
    token, url = join_link(w.db, w.org, performer)
    return performer, token


def _public_headers_ok(r):
    for key, value in HEADERS.items():
        assert r.headers.get(key) == value, (key, r.headers.get(key))


class TestLinks:
    def test_no_token_is_404_and_writes_nothing(self, w, client):
        before = w.db.query(EntPerformer).count()
        for path in ("/api/public/join/not-a-token", "/api/public/join/" + "A" * 43):
            r = client.get(path)
            assert r.status_code == 404
            _public_headers_ok(r)
        r = client.post(
            "/api/public/join/" + "A" * 43 + "/submit", json=full_submission()
        )
        assert r.status_code == 404
        _public_headers_ok(r)
        assert w.db.query(EntPerformer).count() == before

    def test_the_form_contract_carries_no_stored_values(self, w, client):
        performer = add_performer(w.db, w.org)  # an accepted performer re-invited
        token, _url = join_link(w.db, w.org, performer)
        r = client.get(f"/api/public/join/{token}")
        assert r.status_code == 200, r.text
        _public_headers_ok(r)
        body = r.json()
        assert (
            body["purpose"] == "onboarding"
            and body["state"] == "open"
            and body["update"] is True
        )
        assert body["prefill"] == {
            "name": "Sam Smith",
            "email": "sam@example.test",
            "phone": "021 000 000",
        }
        assert body["declaration"]["wording_version"] == DECLARATION_WORDING_VERSION
        assert len(body["confirmations"]) == 6 and body["steps"][-2] == "bank_tax"
        assert_no_sensitive(body)
        assert "12-3456" not in r.text and "049091850" not in r.text

    def test_a_revoked_or_wrong_purpose_link_is_the_same_404(self, w, client):
        performer = add_performer(w.db, w.org)
        token, _url = join_link(w.db, w.org, performer, purpose="portal")
        assert client.get(f"/api/public/join/{token}").status_code == 404
        token, _url = join_link(w.db, w.org, performer)
        row = (
            w.db.query(EntPublicToken)
            .filter(EntPublicToken.token_hash == tokens.hash_token(token))
            .one()
        )
        tokens.revoke(w.db, row)
        assert client.get(f"/api/public/join/{token}").status_code == 404

    def test_module_off_is_404(self, db_session, client):
        w = make_world(db_session, mode="off")
        performer = add_performer(w.db, w.org)
        token, _url = join_link(w.db, w.org, performer)
        assert client.get(f"/api/public/join/{token}").status_code == 404


class TestCode:
    def test_bank_step_and_submit_refuse_without_a_verified_code(
        self, w, client, invited
    ):
        performer, token = invited
        r = client.post(
            f"/api/public/join/{token}/steps/bank_tax", json=full_submission()
        )
        assert r.status_code == 403 and r.json()["detail"]["code"] == "forbidden"
        r = client.post(f"/api/public/join/{token}/submit", json=full_submission())
        assert r.status_code == 403
        w.db.refresh(performer)
        assert (
            performer.bank_account is None and performer.onboarding_status == "invited"
        )

    def test_code_request_is_silent_and_goes_to_the_invited_address(
        self, w, client, invited
    ):
        performer, token = invited
        r = client.post(f"/api/public/join/{token}/code/request")
        assert r.status_code == 202 and r.json() == {"sent": True}
        _public_headers_ok(r)
        link = w.db.query(EntEmailLink).filter(EntEmailLink.kind == "ent_code").one()
        assert link.recipient_email == performer.email
        log = w.db.query(EmailLog).filter(EmailLog.id == link.email_log_id).one()
        assert log.html_body is None  # the code is never written down
        # three sends per window, then silent
        for _ in range(3):
            assert (
                client.post(f"/api/public/join/{token}/code/request").status_code == 202
            )
        assert (
            w.db.query(EntEmailLink).filter(EntEmailLink.kind == "ent_code").count()
            == 3
        )

    def test_right_code_opens_the_step_and_wrong_codes_burn_it(
        self, w, client, invited
    ):
        performer, token = invited
        set_code(w.db, token, "123456")
        r = client.post(
            f"/api/public/join/{token}/code/verify", json={"code": "000000"}
        )
        assert r.status_code == 400 and r.json()["detail"]["code"] == "bad_code"
        r = client.post(
            f"/api/public/join/{token}/code/verify", json={"code": "123456"}
        )
        assert r.status_code == 200 and r.json() == {"verified": True}
        assert (
            client.get(f"/api/public/join/{token}").json()["code"]["verified"] is True
        )
        r = client.post(
            f"/api/public/join/{token}/steps/bank_tax", json=full_submission()
        )
        assert r.status_code == 200, r.text

    def test_five_wrong_tries_burn_the_code(self, w, client, invited):
        _performer, token = invited
        set_code(w.db, token, "123456")
        for _ in range(5):
            assert (
                client.post(
                    f"/api/public/join/{token}/code/verify", json={"code": "111111"}
                ).status_code
                == 400
            )
        assert (
            client.post(
                f"/api/public/join/{token}/code/verify", json={"code": "123456"}
            ).status_code
            == 400
        )

    def test_the_sixth_wrong_code_of_the_day_is_refused_even_when_right(
        self, w, client, invited
    ):
        _performer, token = invited
        # five wrong across two codes, then a fresh right code is still refused
        set_code(w.db, token, "123456")
        for _ in range(3):
            client.post(
                f"/api/public/join/{token}/code/verify", json={"code": "111111"}
            )
        set_code(w.db, token, "222222")
        for _ in range(2):
            client.post(
                f"/api/public/join/{token}/code/verify", json={"code": "111111"}
            )
        set_code(w.db, token, "333333")
        assert (
            tokens.failures_today(
                w.db,
                w.db.query(EntPublicToken)
                .filter(EntPublicToken.token_hash == tokens.hash_token(token))
                .one()
                .id,
            )
            == 5
        )
        r = client.post(
            f"/api/public/join/{token}/code/verify", json={"code": "333333"}
        )
        assert r.status_code == 400


class TestSubmit:
    def test_submit_writes_the_performer_and_shows_nothing_back(
        self, w, client, invited
    ):
        performer, token = invited
        verified(w.db, token)
        r = client.post(f"/api/public/join/{token}/submit", json=full_submission())
        assert r.status_code == 200, r.text
        assert r.json() == {"state": "submitted"}
        _public_headers_ok(r)
        w.db.refresh(performer)
        assert (
            performer.onboarding_status == "submitted"
            and performer.submitted_at is not None
        )
        assert performer.lane == "payroll_contractor"
        assert (
            performer.bank_account == "12-3456-7890138-00"
            and performer.ird_number == "049091850"
        )
        assert performer.date_of_birth == "1990-01-02" and performer.tax_code == "WT"
        snap = performer.declaration_snapshot
        assert (
            snap["wording_version"] == DECLARATION_WORDING_VERSION
            and snap["signature"] == "Samuel Smith"
        )
        assert (
            snap["ip_hash"]
            and snap["signed_at"]
            and snap["wt_rate_bp"] == 2000
            and snap["method"] == "form"
        )
        assert (
            "I declare that the information I have provided is true and correct."
            in snap["paragraphs"]
        )
        assert performer.contractor_confirmations["own_tax"] is True
        # the page now says received; nothing stored is in it
        r = client.get(f"/api/public/join/{token}")
        assert r.json()["state"] == "submitted"
        assert not any(v in r.text for v in SECRET_VALUES)

    def test_a_spent_link_refuses_a_second_submission(self, w, client, invited):
        performer, token = invited
        verified(w.db, token)
        assert (
            client.post(
                f"/api/public/join/{token}/submit", json=full_submission()
            ).status_code
            == 200
        )
        r = client.post(
            f"/api/public/join/{token}/submit",
            json=full_submission(bank_account="99-9999-9999999-99"),
        )
        assert r.status_code == 409 and r.json()["detail"]["code"] == "conflict"
        w.db.refresh(performer)
        assert performer.bank_account == "12-3456-7890138-00"
        assert (
            client.post(
                f"/api/public/join/{token}/steps/about",
                json={"name": "X", "email": "x@x.test", "phone": "1"},
            ).status_code
            == 409
        )

    def test_a_bad_ird_or_bank_check_digit_is_refused(self, w, client, invited):
        _performer, token = invited
        verified(w.db, token)
        r = client.post(
            f"/api/public/join/{token}/submit",
            json=full_submission(ird_number="49091851"),
        )
        assert r.status_code == 400
        assert "ird_number" in r.json()["detail"]["errors"]
        # A bank account with the right shape but a wrong digit is refused
        # in plain words, on the bank step and on the submit.
        for path in ("steps/bank_tax", "submit"):
            r = client.post(
                f"/api/public/join/{token}/{path}",
                json=full_submission(bank_account="12-3456-7890123-00"),
            )
            assert r.status_code == 400
            assert r.json()["detail"]["errors"]["bank_account"] == (
                "That bank account number doesn't check out — please re-check it."
            )

    def test_gst_comes_from_the_registration_answer_whatever_the_lane(
        self, w, client, invited
    ):
        performer, token = invited
        verified(w.db, token)
        body = full_submission(
            paid_as="company",
            company_name="Smith Sounds Limited",
            gst_registered=True,
            gst_number="49091850",
        )
        body.pop("declaration")
        r = client.post(f"/api/public/join/{token}/submit", json=body)
        assert r.status_code == 200, r.text
        w.db.refresh(performer)
        assert (
            performer.lane == "invoice"
            and performer.gst_registered is True
            and performer.gst_number == "049091850"
        )
        # an individual who says they're registered is registered, in the payroll lane
        p2 = add_performer(
            w.db,
            w.org,
            name="Two",
            email="two@example.test",
            status="invited",
            lane=None,
            declaration=False,
        )
        token2, _ = join_link(w.db, w.org, p2)
        verified(w.db, token2)
        r = client.post(
            f"/api/public/join/{token2}/submit",
            json=full_submission(
                name="Two",
                email="two@example.test",
                gst_registered=True,
                gst_number="35901981",
            ),
        )
        assert r.status_code == 200, r.text
        w.db.refresh(p2)
        assert p2.lane == "payroll_contractor" and p2.gst_registered is True
        # registered with no number is a field error
        p3 = add_performer(
            w.db,
            w.org,
            name="Three",
            email="three@example.test",
            status="invited",
            lane=None,
            declaration=False,
        )
        token3, _ = join_link(w.db, w.org, p3)
        verified(w.db, token3)
        r = client.post(
            f"/api/public/join/{token3}/submit",
            json=full_submission(name="Three", gst_registered=True),
        )
        assert r.status_code == 400 and "gst_number" in r.json()["detail"]["errors"]

    def test_not_sure_is_submitted_with_a_review_flag_and_no_lane(
        self, w, client, invited
    ):
        performer, token = invited
        verified(w.db, token)
        body = full_submission(paid_as="not_sure")
        body.pop("declaration")
        r = client.post(f"/api/public/join/{token}/submit", json=body)
        assert r.status_code == 200, r.text
        w.db.refresh(performer)
        assert (
            performer.lane is None
            and performer.needs_review is True
            and "Not sure" in performer.review_reason
        )

    def test_a_non_resident_is_stopped(self, w, client, invited):
        _performer, token = invited
        verified(w.db, token)
        body = full_submission()
        body["declaration"]["nz_tax_resident"] = False
        r = client.post(f"/api/public/join/{token}/submit", json=body)
        assert (
            r.status_code == 400
            and "declaration.nz_tax_resident" in r.json()["detail"]["errors"]
        )

    def test_submitted_is_not_payable_no_pay_line_no_bill(self, w, client, invited):
        performer, token = invited
        verified(w.db, token)
        assert (
            client.post(
                f"/api/public/join/{token}/submit", json=full_submission()
            ).status_code
            == 200
        )
        w.db.refresh(performer)
        perf = approved(w.db, w.org, w.venue, performer)
        ctx = w.ctx()
        lines, skipped = payments.payroll_lines(
            w.db, ctx, w.venue.id, dt.date(2026, 10, 12), with_skipped=True
        )
        assert lines == [] and skipped[0]["reason"].startswith("submitted")
        from app.services.entertainers import bills
        from app.services.finance_core.errors import Conflict

        performer.lane = "invoice"
        w.db.flush()
        with pytest.raises(Conflict, match="isn't payable"):
            bills._target(w.db, ctx, perf.id, None)
        # accept — a team member cannot, finance can — and the line appears
        from app.services.entertainers import performers as performers_svc

        with pytest.raises(Forbidden):
            performers_svc.accept(w.db, w.ctx(w.team_member), performer.id)
        performer.lane = "payroll_contractor"
        performers_svc.accept(w.db, ctx, performer.id)
        lines = payments.payroll_lines(w.db, ctx, w.venue.id, dt.date(2026, 10, 12))
        assert [ln.performance_id for ln in lines] == [perf.id]

    def test_a_bank_change_holds_the_performer_and_emails_the_previous_address(
        self, w, client
    ):
        performer = add_performer(w.db, w.org, email="old@example.test")
        token, _url = join_link(w.db, w.org, performer)
        verified(w.db, token)
        body = full_submission(
            email="new@example.test", bank_account="12-3456-7777725-00"
        )
        r = client.post(f"/api/public/join/{token}/submit", json=body)
        assert r.status_code == 200, r.text
        w.db.refresh(performer)
        assert (
            performer.bank_changed_at is not None
            and performer.onboarding_status == "submitted"
        )
        assert (
            performer.bank_account == "12-3456-7777725-00"
            and performer.email == "new@example.test"
        )
        link = (
            w.db.query(EntEmailLink)
            .filter(EntEmailLink.kind == "ent_details_changed")
            .one()
        )
        assert link.recipient_email == "old@example.test"
        perf = approved(w.db, w.org, w.venue, performer)
        lines = payments.payroll_lines(w.db, w.ctx(), w.venue.id, dt.date(2026, 10, 12))
        assert lines == []
        from app.services.entertainers import performers as performers_svc

        performers_svc.accept(w.db, w.ctx(), performer.id)
        w.db.refresh(performer)
        assert performer.bank_changed_at is None
        assert [
            ln.performance_id
            for ln in payments.payroll_lines(
                w.db, w.ctx(), w.venue.id, dt.date(2026, 10, 12)
            )
        ] == [perf.id]

    def test_an_unchanged_bank_account_on_an_update_is_not_a_change(self, w, client):
        performer = add_performer(w.db, w.org)
        token, _url = join_link(w.db, w.org, performer)
        verified(w.db, token)
        assert (
            client.post(
                f"/api/public/join/{token}/submit",
                json=full_submission(phone="new phone"),
            ).status_code
            == 200
        )
        w.db.refresh(performer)
        assert performer.bank_changed_at is None and performer.phone == "new phone"
        assert (
            w.db.query(EntEmailLink)
            .filter(EntEmailLink.kind == "ent_details_changed")
            .count()
            == 0
        )


class TestBandMembers:
    def _band(self, w, client):
        band = add_performer(
            w.db,
            w.org,
            name="The Band",
            email="band@example.test",
            status="invited",
            lane=None,
            performer_type="band",
            declaration=False,
            bank=None,
            ird=None,
        )
        token, _url = join_link(w.db, w.org, band)
        verified(w.db, token)
        body = full_submission(
            name="The Band",
            email="band@example.test",
            performer_type="band",
            paid_as="not_sure",
            band_mode="members_split",
            members=[
                {"name": "Ann", "email": "ann@example.test"},
                {"name": "Bob", "email": "bob@example.test"},
            ],
        )
        body.pop("declaration")
        body.pop("bank_account")
        r = client.post(f"/api/public/join/{token}/submit", json=body)
        assert r.status_code == 200, r.text
        w.db.refresh(band)
        return band

    def test_members_get_own_links_and_equal_shares(self, w, client):
        band = self._band(w, client)
        members = (
            w.db.query(EntBandMember)
            .filter(EntBandMember.band_performer_id == band.id)
            .order_by(EntBandMember.sort_order)
            .all()
        )
        assert [m.share_bp for m in members] == [
            5000,
            5000,
        ] and band.band_mode == "members_split"
        assert (
            band.needs_review is True
            and "pending member onboarding" in band.review_reason
        )
        assert (
            w.db.query(EntEmailLink)
            .filter(EntEmailLink.kind == "ent_band_invite")
            .count()
            == 2
        )
        assert (
            w.db.query(EntPublicToken)
            .filter(EntPublicToken.purpose == "band_member")
            .count()
            == 2
        )

    def test_a_members_link_writes_only_that_member(self, w, client):
        band = self._band(w, client)
        ann, bob = (
            w.db.query(EntBandMember)
            .filter(EntBandMember.band_performer_id == band.id)
            .order_by(EntBandMember.sort_order)
            .all()
        )
        token, _url = join_link(w.db, w.org, band, purpose="band_member", member=ann)
        form = client.get(f"/api/public/join/{token}").json()
        assert (
            form["purpose"] == "band_member"
            and form["band_name"] == "The Band"
            and "band" not in form["steps"]
        )
        assert form["prefill"]["name"] == "Ann"
        verified(w.db, token)
        before = w.db.query(EntPerformer).count()
        r = client.post(
            f"/api/public/join/{token}/submit",
            json=full_submission(
                name="Ann Member",
                email="ann@example.test",
                legal_name="Ann Member",
                declaration={
                    "nz_tax_resident": True,
                    "wt_rate_type": "standard",
                    "signature": "Ann Member",
                    "confirmed": True,
                },
            ),
        )
        assert r.status_code == 200, r.text
        w.db.refresh(ann)
        w.db.refresh(bob)
        w.db.refresh(band)
        assert ann.member_performer_id and ann.invite_status == "submitted"
        assert bob.member_performer_id is None and bob.invite_status == "invited"
        assert w.db.query(EntPerformer).count() == before + 1
        member_row = (
            w.db.query(EntPerformer)
            .filter(EntPerformer.id == ann.member_performer_id)
            .one()
        )
        assert (
            member_row.lane == "payroll_contractor"
            and member_row.bank_account == "12-3456-7890138-00"
        )
        assert (
            band.bank_account is None and band.needs_review is True
        )  # Bob is still out
        # a second member submission on Ann's link is refused
        assert (
            client.post(
                f"/api/public/join/{token}/submit", json=full_submission()
            ).status_code
            == 409
        )

    def test_band_payable_only_when_every_member_accepted(self, w, client):
        band = self._band(w, client)
        from app.services.entertainers import performers as performers_svc

        ok, why = performers_svc.payable(w.db, band)
        assert ok is False and why.startswith("submitted")
        performers_svc.accept(w.db, w.ctx(), band.id)
        ok, why = performers_svc.payable(w.db, band)
        assert ok is False and "has not submitted" in why


class TestDeclarationLink:
    def test_declaration_only_link_signs_the_ir330c(self, w, client):
        performer = add_performer(w.db, w.org, declaration=False)
        token, _url = join_link(w.db, w.org, performer, purpose="declaration")
        form = client.get(f"/api/public/join/{token}").json()
        assert form["steps"] == ["code", "declaration", "review"]
        r = client.post(
            f"/api/public/join/{token}/submit",
            json={
                "declaration": {
                    "nz_tax_resident": True,
                    "wt_rate_type": "self_selected",
                    "wt_rate_percent": 25,
                    "signature": "Sam Smith",
                    "confirmed": True,
                }
            },
        )
        assert r.status_code == 403
        verified(w.db, token)
        r = client.post(
            f"/api/public/join/{token}/submit",
            json={
                "declaration": {
                    "nz_tax_resident": True,
                    "wt_rate_type": "self_selected",
                    "wt_rate_percent": 25,
                    "signature": "Sam Smith",
                    "confirmed": True,
                }
            },
        )
        assert r.status_code == 200, r.text
        w.db.refresh(performer)
        assert (
            performer.declaration_snapshot["wt_rate_bp"] == 2500
            and performer.wt_rate_type == "self_selected"
        )
        assert performer.ird_number == "049091850"  # kept, not re-entered
        assert_no_sensitive(r.json())
