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

    def test_list_documents_without_auth_returns_401(self, client):
        resp = client.get(f"/api/threads/{uuid.uuid4()}/working-documents")
        assert resp.status_code in (401, 403)


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
