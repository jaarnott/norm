"""Tests for the Norm-wide document upload + recipe-extraction endpoints.

``POST /uploads`` stores a document (bytes in the DB) and returns a handle;
``POST /uploads/{id}/extract-recipe`` runs the recipe extractor over the
uploader's own document. The extractor itself calls the LLM, so it's
monkeypatched here — these tests cover the storage, ownership, and validation
plumbing, not the model output (that's exercised live).
"""

from app.routers import uploads as uploads_router


def _headers(client, db_session):
    """A logged-in non-admin user's auth headers."""
    from tests.conftest import _make_user
    from app.auth.security import create_access_token

    user = _make_user(db_session)
    token = create_access_token({"sub": user.id})
    return {"Authorization": f"Bearer {token}"}, user


class TestUpload:
    def test_upload_returns_handle(self, client, db_session):
        headers, _ = _headers(client, db_session)
        res = client.post(
            "/api/uploads",
            headers=headers,
            files={"file": ("recipe.pdf", b"%PDF-1.4 hello", "application/pdf")},
            data={"extraction_target": "recipe"},
        )
        assert res.status_code == 200, res.text
        body = res.json()
        assert body["id"]
        assert body["content_type"] == "application/pdf"
        assert body["size"] == len(b"%PDF-1.4 hello")
        assert body["extraction_target"] == "recipe"

    def test_upload_rejects_empty(self, client, db_session):
        headers, _ = _headers(client, db_session)
        res = client.post(
            "/api/uploads",
            headers=headers,
            files={"file": ("empty.pdf", b"", "application/pdf")},
        )
        assert res.status_code == 400

    def test_upload_rejects_oversize(self, client, db_session, monkeypatch):
        monkeypatch.setattr(uploads_router, "_MAX_BYTES", 4)
        headers, _ = _headers(client, db_session)
        res = client.post(
            "/api/uploads",
            headers=headers,
            files={"file": ("big.pdf", b"more than four bytes", "application/pdf")},
        )
        assert res.status_code == 413

    def test_upload_requires_auth(self, client):
        res = client.post(
            "/api/uploads",
            files={"file": ("recipe.pdf", b"data", "application/pdf")},
        )
        assert res.status_code in (401, 403)


class TestExtractRecipe:
    def _upload(self, client, headers, content=b"%PDF-1.4 hello"):
        return client.post(
            "/api/uploads",
            headers=headers,
            files={"file": ("recipe.pdf", content, "application/pdf")},
            data={"extraction_target": "recipe"},
        ).json()["id"]

    def test_extract_returns_recipe(self, client, db_session, monkeypatch):
        headers, _ = _headers(client, db_session)
        doc_id = self._upload(client, headers)

        fake = {"name": "Roasted Carrots", "ingredients": [], "method": None}
        monkeypatch.setattr(uploads_router, "extract_recipe", lambda *a, **k: fake)
        res = client.post(f"/api/uploads/{doc_id}/extract-recipe", headers=headers)
        assert res.status_code == 200, res.text
        assert res.json()["recipe"] == fake

    def test_extract_surfaces_extraction_error(self, client, db_session, monkeypatch):
        headers, _ = _headers(client, db_session)
        doc_id = self._upload(client, headers)

        def _boom(*a, **k):
            raise uploads_router.RecipeExtractionError("unsupported type")

        monkeypatch.setattr(uploads_router, "extract_recipe", _boom)
        res = client.post(f"/api/uploads/{doc_id}/extract-recipe", headers=headers)
        assert res.status_code == 400
        assert "unsupported type" in res.json()["detail"]

    def test_extract_is_owner_scoped(self, client, db_session, monkeypatch):
        # Uploaded by user A; user B cannot extract it.
        headers_a, _ = _headers(client, db_session)
        doc_id = self._upload(client, headers_a)

        headers_b, _ = _headers(client, db_session)
        monkeypatch.setattr(
            uploads_router, "extract_recipe", lambda *a, **k: {"name": "x"}
        )
        res = client.post(f"/api/uploads/{doc_id}/extract-recipe", headers=headers_b)
        assert res.status_code == 404

    def test_extract_missing_doc_is_404(self, client, db_session):
        headers, _ = _headers(client, db_session)
        res = client.post("/api/uploads/does-not-exist/extract-recipe", headers=headers)
        assert res.status_code == 404
