"""Tests for working document endpoints."""

import uuid


from app.db.models import Thread, WorkingDocument


class TestListDocuments:
    """GET /api/threads/{thread_id}/working-documents"""

    def test_list_documents(self, client, db_session, admin_user, admin_headers):
        thread = Thread(
            id=str(uuid.uuid4()),
            user_id=admin_user.id,
            domain="procurement",
            status="awaiting_approval",
            intent="place_order.tool_use",
            raw_prompt="Order milk",
        )
        db_session.add(thread)
        db_session.flush()

        doc = WorkingDocument(
            id=str(uuid.uuid4()),
            thread_id=thread.id,
            doc_type="order",
            connector_name="bidfood",
            data={"lines": []},
            version=1,
        )
        db_session.add(doc)
        db_session.flush()

        resp = client.get(
            f"/api/threads/{thread.id}/working-documents", headers=admin_headers
        )
        assert resp.status_code == 200
        data = resp.json()
        assert "documents" in data
        assert len(data["documents"]) == 1
        assert data["documents"][0]["doc_type"] == "order"


class TestGetDocument:
    """GET /api/threads/{thread_id}/working-documents/{doc_id}"""

    def test_get_document(self, client, db_session, admin_user, admin_headers):
        thread = Thread(
            id=str(uuid.uuid4()),
            user_id=admin_user.id,
            domain="hr",
            status="in_progress",
            intent="roster.tool_use",
            raw_prompt="Show roster",
        )
        db_session.add(thread)
        db_session.flush()

        doc = WorkingDocument(
            id=str(uuid.uuid4()),
            thread_id=thread.id,
            doc_type="roster",
            connector_name="deputy",
            data={"rosteredShifts": []},
            version=1,
        )
        db_session.add(doc)
        db_session.flush()

        resp = client.get(
            f"/api/threads/{thread.id}/working-documents/{doc.id}",
            headers=admin_headers,
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["id"] == doc.id
        assert data["doc_type"] == "roster"
        assert data["version"] == 1

    def test_get_document_not_found_returns_404(self, client, admin_headers):
        thread_id = str(uuid.uuid4())
        doc_id = str(uuid.uuid4())
        resp = client.get(
            f"/api/threads/{thread_id}/working-documents/{doc_id}",
            headers=admin_headers,
        )
        assert resp.status_code == 404


class TestPatchDocument:
    """PATCH /api/threads/{thread_id}/working-documents/{doc_id}"""

    def test_patch_document_add_line(
        self, client, db_session, admin_user, admin_headers
    ):
        thread = Thread(
            id=str(uuid.uuid4()),
            user_id=admin_user.id,
            domain="procurement",
            status="in_progress",
            intent="place_order.tool_use",
            raw_prompt="Order stuff",
        )
        db_session.add(thread)
        db_session.flush()

        doc = WorkingDocument(
            id=str(uuid.uuid4()),
            thread_id=thread.id,
            doc_type="order",
            connector_name="bidfood",
            sync_mode="submit",
            data={"lines": []},
            version=1,
        )
        db_session.add(doc)
        db_session.flush()

        resp = client.patch(
            f"/api/threads/{thread.id}/working-documents/{doc.id}",
            json={
                "ops": [
                    {"op": "add_line", "fields": {"product": "Milk", "quantity": 5}}
                ],
                "version": 1,
            },
            headers=admin_headers,
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["version"] == 2
        assert len(data["data"]["lines"]) == 1
        assert data["data"]["lines"][0]["product"] == "Milk"

    def test_patch_document_version_conflict_returns_409(
        self,
        client,
        db_session,
        admin_user,
        admin_headers,
    ):
        thread = Thread(
            id=str(uuid.uuid4()),
            user_id=admin_user.id,
            domain="procurement",
            status="in_progress",
            intent="place_order.tool_use",
            raw_prompt="Order stuff",
        )
        db_session.add(thread)
        db_session.flush()

        doc = WorkingDocument(
            id=str(uuid.uuid4()),
            thread_id=thread.id,
            doc_type="order",
            connector_name="bidfood",
            data={"lines": []},
            version=2,
        )
        db_session.add(doc)
        db_session.flush()

        resp = client.patch(
            f"/api/threads/{thread.id}/working-documents/{doc.id}",
            json={
                "ops": [
                    {"op": "add_line", "fields": {"product": "Milk", "quantity": 5}}
                ],
                "version": 1,  # stale version
            },
            headers=admin_headers,
        )
        assert resp.status_code == 409

    def test_patch_document_not_found_returns_404(self, client, admin_headers):
        resp = client.patch(
            f"/api/threads/{uuid.uuid4()}/working-documents/{uuid.uuid4()}",
            json={"ops": [], "version": 1},
            headers=admin_headers,
        )
        assert resp.status_code == 404


class TestGetStandaloneDocument:
    """GET /api/working-documents/{doc_id}"""

    def test_get_standalone_document(
        self, client, db_session, admin_user, admin_headers
    ):
        doc = WorkingDocument(
            id=str(uuid.uuid4()),
            thread_id=None,
            doc_type="roster",
            connector_name="deputy",
            data={"rosteredShifts": []},
            version=1,
        )
        db_session.add(doc)
        db_session.flush()

        resp = client.get(f"/api/working-documents/{doc.id}", headers=admin_headers)
        assert resp.status_code == 200
        assert resp.json()["id"] == doc.id

    def test_get_standalone_document_not_found_returns_404(self, client, admin_headers):
        resp = client.get(
            f"/api/working-documents/{uuid.uuid4()}", headers=admin_headers
        )
        assert resp.status_code == 404


class TestPatchStandaloneDocument:
    """PATCH /api/working-documents/{doc_id}"""

    def test_patch_standalone_document(
        self, client, db_session, admin_user, admin_headers
    ):
        doc = WorkingDocument(
            id=str(uuid.uuid4()),
            thread_id=None,
            doc_type="order",
            connector_name="bidfood",
            sync_mode="submit",
            data={
                "lines": [
                    {
                        "product": "Bread",
                        "quantity": 2,
                        "unit": "case",
                        "supplier": "",
                        "unit_price": 0,
                    }
                ]
            },
            version=1,
        )
        db_session.add(doc)
        db_session.flush()

        resp = client.patch(
            f"/api/working-documents/{doc.id}",
            json={
                "ops": [{"op": "update_line", "index": 0, "fields": {"quantity": 10}}],
                "version": 1,
            },
            headers=admin_headers,
        )
        assert resp.status_code == 200
        assert resp.json()["version"] == 2
        assert resp.json()["data"]["lines"][0]["quantity"] == 10

    def test_patch_standalone_not_found_returns_404(self, client, admin_headers):
        resp = client.patch(
            f"/api/working-documents/{uuid.uuid4()}",
            json={"ops": [], "version": 1},
            headers=admin_headers,
        )
        assert resp.status_code == 404

    def test_patch_standalone_version_conflict_returns_409(
        self,
        client,
        db_session,
        admin_user,
        admin_headers,
    ):
        doc = WorkingDocument(
            id=str(uuid.uuid4()),
            thread_id=None,
            doc_type="order",
            connector_name="bidfood",
            data={"lines": []},
            version=3,
        )
        db_session.add(doc)
        db_session.flush()

        resp = client.patch(
            f"/api/working-documents/{doc.id}",
            json={"ops": [], "version": 1},
            headers=admin_headers,
        )
        assert resp.status_code == 409


class TestUpsertWorkingDocumentFanOut:
    """_upsert_working_document keyed per item ref (working_document.items_path
    fan-out): one doc per invoice, re-runs update in place, venue_id stamped so
    venue-scoped endpoints (and the Invoices page) find the same draft."""

    def _venue(self, db_session):
        from app.db.models import Venue

        v = Venue(id=str(uuid.uuid4()), name=f"Venue {uuid.uuid4().hex[:6]}")
        db_session.add(v)
        db_session.flush()
        return v.id

    def _thread(self, db_session, admin_user):
        t = Thread(
            id=str(uuid.uuid4()),
            user_id=admin_user.id,
            domain="procurement",
            status="processing",
            intent="receive_invoices.tool_use",
            raw_prompt="Receive the outstanding invoices",
        )
        db_session.add(t)
        db_session.flush()
        return t

    def _upsert(self, db_session, thread, item, venue_id):
        from app.agents.tool_loop import _upsert_working_document

        return _upsert_working_document(
            db_session,
            thread.id,
            "loadedhub",
            {
                "doc_type": "received_invoice",
                "sync_mode": "submit",
                "ref_fields": ["invoice_id"],
            },
            item,
            item,  # fan-out passes the ITEM as the ref source
            venue_id=venue_id,
        )

    def test_one_doc_per_item_ref(self, db_session, admin_user):
        thread = self._thread(db_session, admin_user)
        vid = self._venue(db_session)
        a = self._upsert(db_session, thread, {"invoice_id": "inv-a", "total": 1}, vid)
        b = self._upsert(db_session, thread, {"invoice_id": "inv-b", "total": 2}, vid)
        assert a.id != b.id
        assert a.external_ref == {"invoice_id": "inv-a", "venue_id": vid}
        assert a.venue_id == vid

    def test_rerun_updates_the_same_doc(self, db_session, admin_user):
        thread = self._thread(db_session, admin_user)
        vid = self._venue(db_session)
        a1 = self._upsert(db_session, thread, {"invoice_id": "inv-a", "total": 1}, vid)
        v1 = a1.version
        a2 = self._upsert(db_session, thread, {"invoice_id": "inv-a", "total": 9}, vid)
        assert a2.id == a1.id
        assert a2.data["total"] == 9
        assert a2.version == v1 + 1

    def test_single_doc_path_unchanged_but_gains_venue(self, db_session, admin_user):
        from app.agents.tool_loop import _upsert_working_document

        thread = self._thread(db_session, admin_user)
        vid = self._venue(db_session)
        d1 = _upsert_working_document(
            db_session,
            thread.id,
            "loadedhub",
            {
                "doc_type": "received_invoice",
                "sync_mode": "submit",
                "ref_fields": ["invoice_id"],
            },
            {"x": 1},
            {"invoice_id": "inv-1"},
            venue_id=vid,
        )
        d2 = _upsert_working_document(
            db_session,
            thread.id,
            "loadedhub",
            {
                "doc_type": "received_invoice",
                "sync_mode": "submit",
                "ref_fields": ["invoice_id"],
            },
            {"x": 2},
            {"invoice_id": "inv-1"},
            venue_id=vid,
        )
        assert d2.id == d1.id
        assert d1.venue_id == vid


class TestCrossThreadDocIdentity:
    """Ref-keyed docs are identity-keyed ACROSS threads: one working document
    per (venue, invoice), whichever thread reviews it — per-thread twins were
    how a card could display a review its own doc never carried."""

    def _venue(self, db_session):
        from app.db.models import Venue

        v = Venue(id=str(uuid.uuid4()), name=f"Venue {uuid.uuid4().hex[:6]}")
        db_session.add(v)
        db_session.flush()
        return v.id

    def _thread(self, db_session, admin_user):
        t = Thread(
            id=str(uuid.uuid4()),
            user_id=admin_user.id,
            domain="procurement",
            status="processing",
            intent="receive_invoices.tool_use",
            raw_prompt="Receive the outstanding invoices",
        )
        db_session.add(t)
        db_session.flush()
        return t

    def _upsert(self, db_session, thread, item, venue_id):
        from app.agents.tool_loop import _upsert_working_document

        return _upsert_working_document(
            db_session,
            thread.id,
            "loadedhub",
            {
                "doc_type": "received_invoice",
                "sync_mode": "submit",
                "ref_fields": ["invoice_id"],
            },
            item,
            item,
            venue_id=venue_id,
        )

    def test_second_thread_reuses_the_same_doc(self, db_session, admin_user):
        vid = self._venue(db_session)
        inv = f"inv-{uuid.uuid4().hex[:10]}"
        t1 = self._thread(db_session, admin_user)
        t2 = self._thread(db_session, admin_user)
        a = self._upsert(db_session, t1, {"invoice_id": inv, "total": 1}, vid)
        b = self._upsert(db_session, t2, {"invoice_id": inv, "total": 2}, vid)
        assert b.id == a.id  # no twin
        assert b.data["total"] == 2

    def test_reuse_carries_local_editor_state(self, db_session, admin_user):
        vid = self._venue(db_session)
        inv = f"inv-{uuid.uuid4().hex[:10]}"
        t1 = self._thread(db_session, admin_user)
        doc = self._upsert(
            db_session,
            t1,
            {
                "invoice_id": inv,
                "lines": [{"id": "l-1", "description": "A"}],
            },
            vid,
        )
        # Simulate user edits: strike + local link + added line + action log
        data = dict(doc.data)
        data["actioned_suggestions"] = [{"key": "strike:l-1", "summary": "struck"}]
        data["lines"] = [
            {"id": "l-1", "description": "A", "struck": True},
            {"id": "new-123", "description": "LOCAL ADD"},
        ]
        doc.data = data
        db_session.flush()

        t2 = self._thread(db_session, admin_user)
        doc2 = self._upsert(
            db_session,
            t2,
            {
                "invoice_id": inv,
                "lines": [{"id": "l-1", "description": "A (fresh)"}],
            },
            vid,
        )
        assert doc2.id == doc.id
        by_id = {ln["id"]: ln for ln in doc2.data["lines"]}
        assert by_id["l-1"].get("struck") is True  # strike survived the re-run
        assert "new-123" in by_id  # locally-added line survived
        assert doc2.data["actioned_suggestions"] == [
            {"key": "strike:l-1", "summary": "struck"}
        ]

    def test_received_docs_are_not_reused(self, db_session, admin_user):
        vid = self._venue(db_session)
        inv = f"inv-{uuid.uuid4().hex[:10]}"
        t1 = self._thread(db_session, admin_user)
        old = self._upsert(
            db_session, t1, {"invoice_id": inv, "is_received": True}, vid
        )
        t2 = self._thread(db_session, admin_user)
        fresh = self._upsert(db_session, t2, {"invoice_id": inv, "total": 5}, vid)
        assert fresh.id != old.id
