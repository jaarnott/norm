"""Cross-draft bookkeeping after receiving/deleting an invoice.

A chat batch renders one card per invoice; receiving one used to leave sibling
cards stale — the second copy of a duplicate pair (or a card whose PO just got
invoiced) could be received again. invalidate_conflicting_drafts marks twin
docs of the acted invoice as received and clears conflicting siblings' cached
reviews so their next /review re-runs against the fresh received feed.
"""

import uuid

import pytest

from app.db.models import Venue, WorkingDocument
from app.services.received_invoice import invalidate_conflicting_drafts


@pytest.fixture()
def venue(db_session):
    v = Venue(id=str(uuid.uuid4()), name=f"Venue {uuid.uuid4().hex[:6]}")
    db_session.add(v)
    db_session.flush()
    return v


def _doc(db_session, venue, invoice_id, **data):
    doc = WorkingDocument(
        doc_type="received_invoice",
        connector_name="loadedhub",
        venue_id=venue.id,
        external_ref={"invoice_id": invoice_id},
        data={
            "invoice_id": invoice_id,
            "reviewed_at": "2026-10-01T00:00:00Z",
            "suggestions": [],
            **data,
        },
    )
    db_session.add(doc)
    db_session.flush()
    return doc


class TestInvalidateConflictingDrafts:
    def test_twin_docs_marked_received(self, db_session, venue):
        acted = _doc(db_session, venue, "inv-1", reference_number="R-1")
        twin = _doc(db_session, venue, "inv-1", reference_number="R-1")
        invalidate_conflicting_drafts(db_session, venue.id, "inv-1")
        for d in (acted, twin):
            db_session.refresh(d)
            assert d.data["is_received"] is True
            assert d.data["status"] == "received"

    def test_duplicate_sibling_review_cleared(self, db_session, venue):
        _doc(
            db_session,
            venue,
            "inv-1",
            reference_number="IN11380900",
            supplier_name="Service Foods Auckland",
        )
        dup = _doc(
            db_session,
            venue,
            "inv-2",
            reference_number="IN11380900",
            supplier_name="SERVICE FOODS AUCKLAND",
            suggestions=[{"type": "link_po"}],
        )
        invalidate_conflicting_drafts(db_session, venue.id, "inv-1")
        db_session.refresh(dup)
        assert dup.data["reviewed_at"] is None
        # Its suggestions stay until the next review: that review carries the
        # decisions made on them, and needs the questions they answered.
        assert dup.data["suggestions"] == [{"type": "link_po"}]
        assert not dup.data.get("is_received")

    def test_po_overlap_sibling_cleared(self, db_session, venue):
        """A sibling whose review SUGGESTS linking the order just invoiced is
        cleared, in the shape the replica review writes today
        (``apply.linked_purchase_order_id``) as well as the legacy top-level
        ``purchase_order_id`` older drafts may still carry. Only the legacy
        shape used to be read, so a current suggestion never matched."""
        _doc(
            db_session,
            venue,
            "inv-1",
            reference_number="R-1",
            linked_purchase_order_id="po-9",
        )
        legacy = _doc(
            db_session,
            venue,
            "inv-2",
            reference_number="R-2",
            suggestions=[{"type": "link_po", "purchase_order_id": "po-9"}],
        )
        current = _doc(
            db_session,
            venue,
            "inv-3",
            reference_number="R-3",
            suggestions=[
                {
                    "id": "link_po:linked_purchase_order_id",
                    "kind": "link_po",
                    "field": "linked_purchase_order_id",
                    "proposed": "1520999",
                    "apply": {
                        "linked_purchase_order_id": "po-9",
                        "purchase_order_number": "1520999",
                    },
                }
            ],
        )
        invalidate_conflicting_drafts(db_session, venue.id, "inv-1")
        for d in (legacy, current):
            db_session.refresh(d)
            assert d.data["reviewed_at"] is None, d.data["invoice_id"]
            assert d.data["suggestions"]  # kept for the next review's carry

    def test_the_acted_invoices_own_link_suggestion_counts_too(self, db_session, venue):
        """The received invoice's own (current-shape) link suggestion names
        the order it took, so a sibling already linked to it is cleared."""
        _doc(
            db_session,
            venue,
            "inv-1",
            reference_number="R-1",
            suggestions=[
                {
                    "kind": "link_po",
                    "apply": {
                        "linked_purchase_order_id": "po-9",
                        "purchase_order_number": "1520999",
                    },
                }
            ],
        )
        sibling = _doc(
            db_session,
            venue,
            "inv-2",
            reference_number="R-2",
            purchase_order_number="1520999",
        )
        invalidate_conflicting_drafts(db_session, venue.id, "inv-1")
        db_session.refresh(sibling)
        assert sibling.data["reviewed_at"] is None

    def test_unrelated_sibling_untouched(self, db_session, venue):
        _doc(db_session, venue, "inv-1", reference_number="R-1")
        other = _doc(
            db_session,
            venue,
            "inv-2",
            reference_number="R-2",
            supplier_name="Someone Else",
        )
        invalidate_conflicting_drafts(db_session, venue.id, "inv-1")
        db_session.refresh(other)
        assert other.data["reviewed_at"] == "2026-10-01T00:00:00Z"

    def test_delete_path_uses_caller_identity(self, db_session, venue):
        # The deleted invoice's own docs are already gone — the caller passes
        # reference/supplier explicitly; received=False must not mark twins.
        dup = _doc(
            db_session,
            venue,
            "inv-2",
            reference_number="IN11380901",
            supplier_name="Service Foods Auckland",
        )
        invalidate_conflicting_drafts(
            db_session,
            venue.id,
            "inv-1",
            reference_number="IN11380901",
            supplier_name="Service Foods Auckland",
            received=False,
        )
        db_session.refresh(dup)
        assert dup.data["reviewed_at"] is None
        assert not dup.data.get("is_received")

    def test_already_received_siblings_skipped(self, db_session, venue):
        done = _doc(
            db_session, venue, "inv-2", reference_number="R-1", is_received=True
        )
        _doc(db_session, venue, "inv-1", reference_number="R-1")
        invalidate_conflicting_drafts(db_session, venue.id, "inv-1")
        db_session.refresh(done)
        assert done.data["reviewed_at"] == "2026-10-01T00:00:00Z"  # untouched

    def test_never_raises_on_malformed_docs(self, db_session, venue):
        doc = WorkingDocument(
            doc_type="received_invoice",
            connector_name="loadedhub",
            venue_id=venue.id,
            external_ref=None,
            data=None,
        )
        db_session.add(doc)
        db_session.flush()
        invalidate_conflicting_drafts(db_session, venue.id, "inv-1")  # no raise


class TestReceiveEndpointWiring:
    """/invoice-fixes/receive takes the one receive path
    (``received_invoice.receive_from_draft``) — body ``{venue_id, invoice_id}``."""

    def _wire(self, monkeypatch, received):
        import app.routers.invoice_fixes as IF

        monkeypatch.setattr(IF, "_Loaded", lambda db, cdb, vid: object())
        monkeypatch.setattr(
            "app.services.received_invoice.do_receive",
            lambda lh, req: {"ok": True, "received": received},
        )

    def test_receive_invalidates_siblings(
        self, client, db_session, admin_user, admin_headers, monkeypatch, venue
    ):
        _doc(db_session, venue, "inv-1", reference_number="R-1", supplier_name="S")
        dup = _doc(
            db_session, venue, "inv-2", reference_number="R-1", supplier_name="S"
        )
        self._wire(monkeypatch, received=True)
        resp = client.post(
            "/api/invoice-fixes/receive",
            json={"venue_id": venue.id, "invoice_id": "inv-1"},
            headers=admin_headers,
        )
        assert resp.status_code == 200
        db_session.refresh(dup)
        assert dup.data["reviewed_at"] is None

    def test_the_card_receive_creates_a_xero_bill_only_for_a_person_who_asked(
        self, client, db_session, admin_user, admin_headers, monkeypatch, venue
    ):
        """The card's Accept & Receive reads the CALLER's receiving settings:
        the Xero step is enabled for someone who switched "create a bill in
        Xero" on, and not for someone who didn't — and never on a refused
        receive."""
        from app.services import approvals

        seen = []
        monkeypatch.setattr(
            "app.services.xero_bills.push_bill_after_receive",
            lambda db, cdb, venue_id, iid, data, **kw: seen.append(
                (iid, kw.get("enabled"))
            ),
        )
        self._wire(monkeypatch, received=True)

        def receive(iid):
            _doc(db_session, venue, iid, reference_number=f"R-{iid}", supplier_name="S")
            return client.post(
                "/api/invoice-fixes/receive",
                json={"venue_id": venue.id, "invoice_id": iid},
                headers=admin_headers,
            )

        assert receive("inv-a").status_code == 200  # never switched it on
        admin_user.approval_preferences = {
            approvals.RECEIVE_KEY: {
                "level": "approve_all",  # not Autopilot: the card creates it too
                "options": {"create_xero_bill": True},
            }
        }
        db_session.flush()
        assert receive("inv-b").status_code == 200
        assert seen == [("inv-a", False), ("inv-b", True)]

        seen.clear()
        self._wire(monkeypatch, received=False)  # Loaded refuses: nothing follows
        assert receive("inv-c").status_code == 200
        assert seen == []

    def test_a_refused_receive_does_not_invalidate(
        self, client, db_session, admin_user, admin_headers, monkeypatch, venue
    ):
        _doc(db_session, venue, "inv-1", reference_number="R-1", supplier_name="S")
        dup = _doc(
            db_session, venue, "inv-2", reference_number="R-1", supplier_name="S"
        )
        self._wire(monkeypatch, received=False)
        resp = client.post(
            "/api/invoice-fixes/receive",
            json={"venue_id": venue.id, "invoice_id": "inv-1"},
            headers=admin_headers,
        )
        assert resp.status_code == 200
        db_session.refresh(dup)
        assert dup.data["reviewed_at"] == "2026-10-01T00:00:00Z"

    def test_no_open_draft_is_a_404(self, client, admin_headers, monkeypatch, venue):
        self._wire(monkeypatch, received=True)
        resp = client.post(
            "/api/invoice-fixes/receive",
            json={"venue_id": venue.id, "invoice_id": "never-opened"},
            headers=admin_headers,
        )
        assert resp.status_code == 404


class TestResetValidation:
    """POST /invoice-fixes/reset-validation wipes the invoice's cached review,
    extraction rows and action log, rebuilding drafts fresh from Loaded so
    validation can be retested from scratch."""

    def test_wipes_extractions_and_rebuilds_drafts(
        self, client, db_session, admin_user, admin_headers, monkeypatch, venue
    ):
        import app.routers.invoice_fixes as IF
        from app.db.models import DocumentExtraction

        ref = f"R-{uuid.uuid4().hex[:10]}"
        detail = {
            "id": "inv-1",
            "referenceNumber": ref,
            "supplierName": "S",
            "lines": [],
        }

        class FakeLh:
            def invoice(self, iid):
                return detail

        monkeypatch.setattr(IF, "_Loaded", lambda db, cdb, vid: FakeLh())

        db_session.add(
            DocumentExtraction(
                cache_key=f"k1-{ref}",
                connector="loadedhub",
                action="download_invoice_file",
                data={"invoice_number": ref, "lines": []},
            )
        )
        db_session.add(
            DocumentExtraction(
                cache_key=f"k2-{ref}",
                connector="loadedhub",
                action="download_invoice_file",
                data={"invoice_number": "SOMETHING-ELSE"},
            )
        )
        doc = WorkingDocument(
            doc_type="received_invoice",
            connector_name="loadedhub",
            venue_id=venue.id,
            external_ref={"invoice_id": "inv-1"},
            data={
                "invoice_id": "inv-1",
                "reviewed_at": "2026-10-01T00:00:00Z",
                "suggestions": [{"type": "unit"}],
                "actioned_suggestions": [{"key": "unit:l1", "summary": "done"}],
                "lines": [{"id": "l1", "struck": True}],
            },
        )
        db_session.add(doc)
        db_session.flush()

        resp = client.post(
            "/api/invoice-fixes/reset-validation",
            json={"venue_id": venue.id, "invoice_id": "inv-1"},
            headers=admin_headers,
        )
        assert resp.status_code == 200
        out = resp.json()
        assert out["extractions_deleted"] == 1
        assert out["documents_reset"] == 1

        from app.db.models import DocumentExtraction as DE

        assert db_session.query(DE).filter(DE.cache_key == f"k1-{ref}").count() == 0
        assert db_session.query(DE).filter(DE.cache_key == f"k2-{ref}").count() == 1

        db_session.refresh(doc)
        assert not doc.data.get("reviewed_at")
        assert not doc.data.get("actioned_suggestions")
        assert doc.data.get("reference_number") == ref
        # local line state gone too — rebuilt fresh from Loaded
        assert doc.data.get("lines") == []

    def test_received_drafts_left_alone(
        self, client, db_session, admin_user, admin_headers, monkeypatch, venue
    ):
        import app.routers.invoice_fixes as IF

        class FakeLh:
            def invoice(self, iid):
                return {"id": "inv-1", "referenceNumber": "R-X", "lines": []}

        monkeypatch.setattr(IF, "_Loaded", lambda db, cdb, vid: FakeLh())
        done = _doc(db_session, venue, "inv-1", is_received=True)
        resp = client.post(
            "/api/invoice-fixes/reset-validation",
            json={"venue_id": venue.id, "invoice_id": "inv-1"},
            headers=admin_headers,
        )
        assert resp.status_code == 200
        assert resp.json()["documents_reset"] == 0
        db_session.refresh(done)
        assert done.data["reviewed_at"] == "2026-10-01T00:00:00Z"


class TestDeleteTombstones:
    """Accepting delete_invoice must leave TOMBSTONE docs (is_deleted), not
    hard-delete them — old chat threads still hold cards pointing at the doc
    ids, and a card must render 'deleted', not hang on a 404."""

    def test_delete_invoice_accept_marks_docs_deleted(
        self, client, db_session, admin_user, admin_headers, monkeypatch, venue
    ):
        import app.routers.invoice_fixes as IF

        monkeypatch.setattr(IF, "_Loaded", lambda db, cdb, vid: object())
        monkeypatch.setitem(
            IF._APPLIERS, "delete_invoice", lambda lh, fix, db: "Draft deleted"
        )
        outcomes = []
        monkeypatch.setattr(
            "app.services.autopilot_metrics.record_receive_outcome",
            lambda db, **kw: outcomes.append(kw),
        )
        doc = _doc(
            db_session, venue, "inv-1", reference_number="R-1", supplier_name="S"
        )
        twin = _doc(
            db_session, venue, "inv-1", reference_number="R-1", supplier_name="S"
        )
        sibling = _doc(
            db_session, venue, "inv-2", reference_number="R-1", supplier_name="S"
        )

        resp = client.post(
            "/api/invoice-fixes/accept",
            json={
                "venue_id": venue.id,
                "invoice_id": "inv-1",
                "fix": {
                    "type": "delete_invoice",
                    "invoice_id": "inv-1",
                    "summary": "duplicate — delete this draft",
                },
            },
            headers=admin_headers,
        )
        assert resp.status_code == 200
        assert resp.json()["deleted"] is True

        for d in (doc, twin):
            db_session.refresh(d)
            assert d.data["is_deleted"] is True
            assert d.data["status"] == "deleted"
            assert "duplicate" in d.data["deleted_reason"]
        # The same-numbered SIBLING is refreshed (review cleared), never tombstoned.
        db_session.refresh(sibling)
        assert sibling.data["reviewed_at"] is None
        assert not sibling.data.get("is_deleted")
        # A person's delete is an outcome, so the activity page stops listing
        # the invoice as stopped (review of the Activity page, 19 Oct 2026).
        (o,) = outcomes
        assert (o["outcome_override"], o["actor"], o["received"]) == (
            "deleted",
            "user",
            False,
        )
        assert "duplicate" in o["extra_detail"]["deleted"]["reason"]

    def test_tombstones_are_skipped_by_reset_and_invalidation(
        self, client, db_session, admin_user, admin_headers, monkeypatch, venue
    ):
        import app.routers.invoice_fixes as IF

        tomb = _doc(
            db_session,
            venue,
            "inv-1",
            reference_number="R-1",
            supplier_name="S",
            is_deleted=True,
        )

        # invalidation: a tombstone is neither marked received nor "cleared"
        invalidate_conflicting_drafts(
            db_session, venue.id, "inv-9", reference_number="R-1", supplier_name="S"
        )
        db_session.refresh(tomb)
        assert tomb.data["reviewed_at"] == "2026-10-01T00:00:00Z"
        assert not tomb.data.get("is_received")

        # reset-validation: documents_reset == 0 for a tombstoned invoice
        class FakeLh:
            def invoice(self, iid):
                return {"id": "inv-1", "referenceNumber": "R-XYZQ", "lines": []}

        monkeypatch.setattr(IF, "_Loaded", lambda db, cdb, vid: FakeLh())
        resp = client.post(
            "/api/invoice-fixes/reset-validation",
            json={"venue_id": venue.id, "invoice_id": "inv-1"},
            headers=admin_headers,
        )
        assert resp.status_code == 200
        assert resp.json()["documents_reset"] == 0


class TestReviewTwinHealing:
    """/invoice-fixes/review lands the fresh replica_v2 payload on ALL open
    twin docs (historic per-thread duplicates) and bumps every version — a
    card bound to a non-canonical twin used to PATCH and get a bare doc back,
    visibly losing the validation. Re-review is a SQUASH: the payload replaces
    the doc wholesale (local edits deliberately discarded)."""

    def _twin(self, db_session, venue, inv, **data):
        return _doc(db_session, venue, inv, **data)

    @staticmethod
    def _payload():
        return {
            "doc_schema": "replica_v2",
            "reference_number": "R-1",
            "lines": [{"id": "l-1", "description": "X", "quantity_received": 4}],
            "suggestions": [{"id": "line_value:quantity_received:l-1"}],
            "issues": [],
            "suggestion_actions": [],
            "confidence": "ready",
            "reviewed_at": "2026-08-10T00:00:00+00:00",
            "reviewed_invoice_fingerprint": "fp",
        }

    def test_review_populates_every_twin_and_bumps_versions(
        self, client, db_session, admin_user, admin_headers, monkeypatch, venue
    ):
        inv = f"inv-{uuid.uuid4().hex[:10]}"
        a = self._twin(
            db_session, venue, inv, lines=[{"id": "l-1", "description": "X"}]
        )
        b = self._twin(
            db_session,
            venue,
            inv,
            lines=[{"id": "l-1", "description": "X", "struck": True}],
        )
        va, vb = a.version, b.version

        monkeypatch.setattr(
            "app.services.invoice_review.review_invoice",
            lambda *args, **kwargs: dict(self._payload()),
        )
        import app.routers.invoice_fixes as IF

        monkeypatch.setattr(IF, "_attach_po_reference", lambda *a, **k: None)
        monkeypatch.setattr(IF, "attach_item_names", lambda *a, **k: None)
        monkeypatch.setattr(IF, "_Loaded", lambda db, cdb, vid: object())
        resp = client.post(
            "/api/invoice-fixes/review",
            json={"venue_id": venue.id, "invoice_id": inv},
            headers=admin_headers,
        )
        assert resp.status_code == 200
        db_session.refresh(a)
        db_session.refresh(b)
        for d in (a, b):
            assert d.data["doc_schema"] == "replica_v2"
            assert d.data["suggestions"] == [{"id": "line_value:quantity_received:l-1"}]
            assert d.data["lines"][0]["quantity_received"] == 4
        # Each twin is the fresh payload plus what was done on THAT card: the
        # line struck on b stays struck there (owner, 19 Oct 2026: what a
        # person did is replayed onto the rebuild, never reset), and a is not
        # given b's strike.
        assert b.data["lines"][0].get("struck") is True
        assert a.data["lines"][0].get("struck") is None
        assert a.version == va + 1 and b.version == vb + 1

    def test_reviewed_doc_is_cached_unless_forced(
        self, client, db_session, admin_user, admin_headers, monkeypatch, venue
    ):
        inv = f"inv-{uuid.uuid4().hex[:10]}"
        self._twin(
            db_session,
            venue,
            inv,
            doc_schema="replica_v2",
            reviewed_at="2026-08-10T00:00:00+00:00",
        )

        calls = []

        def fake_review(*args, **kwargs):
            calls.append(1)
            return dict(self._payload())

        monkeypatch.setattr("app.services.invoice_review.review_invoice", fake_review)
        import app.routers.invoice_fixes as IF

        monkeypatch.setattr(IF, "_attach_po_reference", lambda *a, **k: None)
        monkeypatch.setattr(IF, "attach_item_names", lambda *a, **k: None)
        monkeypatch.setattr(IF, "_Loaded", lambda db, cdb, vid: object())
        resp = client.post(
            "/api/invoice-fixes/review",
            json={"venue_id": venue.id, "invoice_id": inv},
            headers=admin_headers,
        )
        assert resp.status_code == 200
        assert calls == []  # cached — the pipeline must not re-run
        resp = client.post(
            "/api/invoice-fixes/review",
            json={"venue_id": venue.id, "invoice_id": inv, "force": True},
            headers=admin_headers,
        )
        assert resp.status_code == 200
        assert calls == [1]  # force = re-run (the squash path)

    def test_a_replayed_order_choice_gets_its_rows(
        self, client, db_session, admin_user, admin_headers, monkeypatch, venue
    ):
        # The person linked PO-B by hand; the review rebuilds the card from
        # Loaded (no order) and replays the choice — and the rows of PO-B are
        # fetched for it, or the receive goes without ordered quantities.
        inv = f"inv-{uuid.uuid4().hex[:10]}"
        self._twin(
            db_session,
            venue,
            inv,
            lines=[{"id": "l-1", "description": "X"}],
            edits=[
                {
                    "seq": 1,
                    "at": "2026-10-19T09:00:00+00:00",
                    "field": "linked_purchase_order_id",
                    "after": "po-B",
                }
            ],
        )
        monkeypatch.setattr(
            "app.services.invoice_review.review_invoice",
            lambda *args, **kwargs: dict(self._payload()),
        )
        import app.routers.invoice_fixes as IF

        fetched = []
        monkeypatch.setattr(
            IF,
            "_attach_po_reference",
            lambda data, lh: fetched.append(data.get("linked_purchase_order_id")),
        )
        monkeypatch.setattr(IF, "attach_item_names", lambda *a, **k: None)
        monkeypatch.setattr(IF, "_Loaded", lambda db, cdb, vid: object())
        resp = client.post(
            "/api/invoice-fixes/review",
            json={"venue_id": venue.id, "invoice_id": inv},
            headers=admin_headers,
        )
        assert resp.status_code == 200
        assert "po-B" in fetched

    def test_a_review_waiting_on_an_answer_is_asked_again(
        self, client, db_session, admin_user, admin_headers, monkeypatch, venue
    ):
        # Norm's answer didn't come through last time: the cached review is
        # not current once it is 10 minutes old, so opening the card asks
        # again (owner, 19 Oct 2026).
        inv = f"inv-{uuid.uuid4().hex[:10]}"
        self._twin(
            db_session,
            venue,
            inv,
            doc_schema="replica_v2",
            reviewed_at="2026-08-10T00:00:00+00:00",
            issues=[
                {
                    "id": "unit_answer_missing:l-1",
                    "code": "unit_answer_missing",
                    "blocking": True,
                    "line_id": "l-1",
                }
            ],
        )
        calls = []

        def fake_review(*args, **kwargs):
            calls.append(1)
            return dict(self._payload())

        monkeypatch.setattr("app.services.invoice_review.review_invoice", fake_review)
        resp = client.post(
            "/api/invoice-fixes/review",
            json={"venue_id": venue.id, "invoice_id": inv},
            headers=admin_headers,
        )
        assert resp.status_code == 200
        assert calls == [1]


class TestLineIdAddressedOps:
    def test_update_line_prefers_line_id_over_stale_index(self):
        from app.routers.working_documents import _apply_op

        data = {
            "lines": [
                {"id": "l-1", "description": "A", "unit_cost": 1},
                {"id": "l-2", "description": "B", "unit_cost": 2},
            ]
        }
        # Stale index 0, but line_id targets l-2 — id must win.
        out = _apply_op(
            data,
            {
                "op": "update_line",
                "index": 0,
                "line_id": "l-2",
                "fields": {"unit_cost": 9},
            },
        )
        assert out["lines"][0]["unit_cost"] == 1
        assert out["lines"][1]["unit_cost"] == 9

    def test_update_line_index_fallback(self):
        from app.routers.working_documents import _apply_op

        data = {"lines": [{"id": "l-1", "unit_cost": 1}]}
        out = _apply_op(
            data, {"op": "update_line", "index": 0, "fields": {"unit_cost": 5}}
        )
        assert out["lines"][0]["unit_cost"] == 5
