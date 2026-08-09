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
            "checks": "pppppppppppp",
            "check_reasons": [],
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
            check_reasons=["old"],
            suggestions=[{"type": "link_po"}],
        )
        invalidate_conflicting_drafts(db_session, venue.id, "inv-1")
        db_session.refresh(dup)
        assert dup.data["checks"] is None
        assert dup.data["check_reasons"] == []
        assert dup.data["suggestions"] == []
        assert not dup.data.get("is_received")

    def test_po_overlap_sibling_cleared(self, db_session, venue):
        _doc(
            db_session,
            venue,
            "inv-1",
            reference_number="R-1",
            linked_purchase_order_id="po-9",
        )
        overlap = _doc(
            db_session,
            venue,
            "inv-2",
            reference_number="R-2",
            suggestions=[{"type": "link_po", "purchase_order_id": "po-9"}],
        )
        invalidate_conflicting_drafts(db_session, venue.id, "inv-1")
        db_session.refresh(overlap)
        assert overlap.data["checks"] is None

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
        assert other.data["checks"] == "pppppppppppp"

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
        assert dup.data["checks"] is None
        assert not dup.data.get("is_received")

    def test_already_received_siblings_skipped(self, db_session, venue):
        done = _doc(
            db_session, venue, "inv-2", reference_number="R-1", is_received=True
        )
        _doc(db_session, venue, "inv-1", reference_number="R-1")
        invalidate_conflicting_drafts(db_session, venue.id, "inv-1")
        db_session.refresh(done)
        assert done.data["checks"] == "pppppppppppp"  # untouched

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
    def test_receive_invalidates_siblings(
        self, client, db_session, admin_user, admin_headers, monkeypatch, venue
    ):
        import app.routers.invoice_fixes as IF

        _doc(db_session, venue, "inv-1", reference_number="R-1", supplier_name="S")
        dup = _doc(
            db_session, venue, "inv-2", reference_number="R-1", supplier_name="S"
        )

        monkeypatch.setattr(IF, "_Loaded", lambda db, cdb, vid: object())
        monkeypatch.setattr(
            IF, "_do_receive", lambda lh, body: {"ok": True, "received": True}
        )

        resp = client.post(
            "/api/invoice-fixes/receive",
            json={"venue_id": venue.id, "invoice_id": "inv-1", "receive": True},
            headers=admin_headers,
        )
        assert resp.status_code == 200
        db_session.refresh(dup)
        assert dup.data["checks"] is None

    def test_save_without_receive_does_not_invalidate(
        self, client, db_session, admin_user, admin_headers, monkeypatch, venue
    ):
        import app.routers.invoice_fixes as IF

        _doc(db_session, venue, "inv-1", reference_number="R-1", supplier_name="S")
        dup = _doc(
            db_session, venue, "inv-2", reference_number="R-1", supplier_name="S"
        )
        monkeypatch.setattr(IF, "_Loaded", lambda db, cdb, vid: object())
        monkeypatch.setattr(
            IF, "_do_receive", lambda lh, body: {"ok": True, "received": False}
        )
        resp = client.post(
            "/api/invoice-fixes/receive",
            json={"venue_id": venue.id, "invoice_id": "inv-1", "receive": False},
            headers=admin_headers,
        )
        assert resp.status_code == 200
        db_session.refresh(dup)
        assert dup.data["checks"] == "pppppppppppp"


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
                "checks": "ppppf",
                "check_reasons": ["old reason"],
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
        assert not doc.data.get("checks")
        assert not doc.data.get("actioned_suggestions")
        assert doc.data.get("reference_number") == ref
        # local line state gone too — rebuilt fresh from Loaded
        assert doc.data.get("lines") == []

    def test_wipes_this_invoices_item_match_cache_only(
        self, client, db_session, admin_user, admin_headers, monkeypatch, venue
    ):
        # A cached wrong/declined stock-item match (the Sailor Jerry case,
        # 08 Aug 2026) used to survive every reset — the rows ARE identifiable
        # per invoice: their data dict is keyed by the invoice's line ids.
        import app.routers.invoice_fixes as IF
        from app.db.models import DocumentExtraction

        detail = {
            "id": "inv-1",
            "referenceNumber": f"R-{uuid.uuid4().hex[:10]}",
            "supplierName": "S",
            "lines": [{"id": "line-abc", "description": "RUM"}],
        }

        class FakeLh:
            def invoice(self, iid):
                return detail

        monkeypatch.setattr(IF, "_Loaded", lambda db, cdb, vid: FakeLh())
        mine = f"m1-{uuid.uuid4().hex[:8]}"
        other = f"m2-{uuid.uuid4().hex[:8]}"
        db_session.add(
            DocumentExtraction(
                cache_key=mine,
                connector="norm",
                action="match_stock_items",
                data={"line-abc": {"matched_item": None, "suggested_name": "Rum"}},
            )
        )
        db_session.add(
            DocumentExtraction(
                cache_key=other,
                connector="norm",
                action="match_stock_items",
                data={"line-of-another-invoice": {"matched_item": None}},
            )
        )
        db_session.flush()

        resp = client.post(
            "/api/invoice-fixes/reset-validation",
            json={"venue_id": venue.id, "invoice_id": "inv-1"},
            headers=admin_headers,
        )
        assert resp.status_code == 200
        assert resp.json()["extractions_deleted"] == 1

        de = db_session.query(DocumentExtraction)
        assert de.filter(DocumentExtraction.cache_key == mine).count() == 0
        assert de.filter(DocumentExtraction.cache_key == other).count() == 1

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
        assert done.data["checks"] == "pppppppppppp"


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
        assert sibling.data["checks"] is None
        assert not sibling.data.get("is_deleted")

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
        assert tomb.data["checks"] == "pppppppppppp"
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
    """/invoice-fixes/review lands the review on ALL open twin docs (historic
    per-thread duplicates), bumps versions, and treats checks="" as cached —
    a card bound to a non-canonical twin used to PATCH and get a bare doc
    back, visibly losing the validation."""

    def _twin(self, db_session, venue, inv, **data):
        return _doc(db_session, venue, inv, **data)

    def test_review_populates_every_twin_and_bumps_versions(
        self, client, db_session, admin_user, admin_headers, monkeypatch, venue
    ):
        import app.routers.invoice_fixes as IF

        inv = f"inv-{uuid.uuid4().hex[:10]}"
        a = self._twin(
            db_session,
            venue,
            inv,
            checks=None,
            lines=[{"id": "l-1", "description": "X"}],
        )
        b = self._twin(
            db_session,
            venue,
            inv,
            checks=None,
            lines=[{"id": "l-1", "description": "X", "struck": True}],
        )
        va, vb = a.version, b.version

        def fake_review(data, venue_id, invoice_id, db, config_db):
            data["checks"] = "ppp"
            data["check_reasons"] = ["r"]
            data["suggestions"] = [{"type": "link_po"}]
            data["reviewed_invoice_fingerprint"] = "fp"
            for ln in data.get("lines") or []:
                ln["copy_quantity"] = 4
                ln["matched_item"] = {"id": "i-1", "name": "M"}

        monkeypatch.setattr(IF, "run_review_and_merge", fake_review)
        resp = client.post(
            "/api/invoice-fixes/review",
            json={"venue_id": venue.id, "invoice_id": inv},
            headers=admin_headers,
        )
        assert resp.status_code == 200
        db_session.refresh(a)
        db_session.refresh(b)
        for d in (a, b):
            assert d.data["checks"] == "ppp"
            assert d.data["suggestions"] == [{"type": "link_po"}]
            assert d.data["lines"][0]["copy_quantity"] == 4
            assert d.data["lines"][0]["matched_item"]["name"] == "M"
        # local state on the twin untouched by the review copy
        assert b.data["lines"][0].get("struck") is True
        assert a.version == va + 1 and b.version == vb + 1

    def test_empty_checks_string_is_cached(
        self, client, db_session, admin_user, admin_headers, monkeypatch, venue
    ):
        import app.routers.invoice_fixes as IF

        inv = f"inv-{uuid.uuid4().hex[:10]}"
        self._twin(db_session, venue, inv, checks="")  # review ran, no artifact

        def boom(*a, **k):
            raise AssertionError("engine must not re-run for checks=''")

        monkeypatch.setattr(IF, "run_review_and_merge", boom)
        resp = client.post(
            "/api/invoice-fixes/review",
            json={"venue_id": venue.id, "invoice_id": inv},
            headers=admin_headers,
        )
        assert resp.status_code == 200
        assert resp.json()["data"]["checks"] == ""


class TestLineIdAddressedOps:
    def test_update_line_prefers_line_id_over_stale_index(self, db_session):
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

    def test_update_line_index_fallback(self, db_session):
        from app.routers.working_documents import _apply_op

        data = {"lines": [{"id": "l-1", "unit_cost": 1}]}
        out = _apply_op(
            data, {"op": "update_line", "index": 0, "fields": {"unit_cost": 5}}
        )
        assert out["lines"][0]["unit_cost"] == 5
