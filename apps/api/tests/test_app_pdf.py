"""Candidate packs (services/app_pdf.py) — the ``pdf`` door built-in apps use
to attach a candidate's papers to an interview invite.

Pinned: a cover page per candidate with their cover letter; PDFs merged page
for page, images fitted to A4, Word documents as text; anything unreadable
listed on a "not included" page instead of sinking the pack (Orbit loses the
whole pack to one macron); a page ceiling; and the door reads and writes only
through the app's own file guard — another org's file never gets in.
"""

import io
import uuid

import pytest
from pypdf import PdfReader

from app.db.models import AppFile, Role
from app.services import app_pdf as AP
from app.services import app_runtime as AR
from app.services.builtin_apps import bind, get_builtin
from tests.conftest import _make_membership, _make_organization, _make_user


def _pages(data):
    return PdfReader(io.BytesIO(data)).pages


def _text(data):
    return "\n".join(p.extract_text() for p in _pages(data))


def _png(w=1200, h=800):
    from PIL import Image

    out = io.BytesIO()
    Image.new("RGB", (w, h), "navy").save(out, "PNG")
    return out.getvalue()


def _docx(text):
    import docx

    d = docx.Document()
    d.add_paragraph(text)
    out = io.BytesIO()
    d.save(out)
    return out.getvalue()


class TestTextPages:
    def test_wraps_and_paginates(self):
        long = "Kia ora, I would love to join the team. " * 400
        data = AP.text_pdf([("title", "Candidate pack — Maia"), ("body", long)])
        pages = _pages(data)
        assert len(pages) > 2
        assert (
            float(pages[0].mediabox.width) == 595
            and float(pages[0].mediabox.height) == 842
        )

    def test_anything_helvetica_cant_draw_degrades_not_fails(self):
        data = AP.text_pdf([("body", "Māori — “quotes” and an emoji 🍺")])
        text = _text(data)
        assert "Maori" in text and "“quotes”" in text and "?" in text

    def test_brackets_and_backslashes_are_escaped(self):
        assert "a (b) \\c" in _text(AP.text_pdf([("body", "a (b) \\c")]))


class TestBuildPack:
    def _files(self):
        return {
            "cv": (
                AP.text_pdf([("body", "CURRICULUM VITAE")]),
                "cv.pdf",
                "application/pdf",
            ),
            "photo": (_png(), "photo.png", "image/png"),
            "word": (_docx("Experience: five years behind the bar"), "cv.docx", None),
            "txt": (b"plain", "notes.txt", "text/plain"),
            "broken": (b"%PDF-1.4 nonsense", "bad.pdf", "application/pdf"),
        }

    def test_cover_then_documents_then_what_was_left_out(self):
        files = self._files()
        pdf, summary = AP.build_candidate_pack(
            [
                {
                    "name": "Maia Ngata",
                    "job_title": "Bar Team",
                    "cover_letter": "I love a busy Friday.",
                    "documents": [
                        {"file_name": k, "file_id": k}
                        for k in ("cv", "photo", "word", "txt", "broken")
                    ]
                    + [{"file_name": "orbit-only.pdf", "file_id": None}],
                }
            ],
            lambda fid: files.get(fid),
        )
        assert summary["included"] == ["cv", "photo", "word"]
        assert {s["file"]: s["reason"] for s in summary["skipped"]} == {
            "txt": "file type",
            "broken": "could not be read",
            "orbit-only.pdf": "not available",
        }
        pages = _pages(pdf)
        # cover, cv, photo, word text, not-included
        assert len(pages) == summary["pages"] == 5
        assert "Candidate pack — Maia Ngata" in pages[0].extract_text()
        assert "I love a busy Friday." in pages[0].extract_text()
        assert "CURRICULUM VITAE" in pages[1].extract_text()
        assert "five years behind the bar" in pages[3].extract_text()
        assert "Not included in this pack" in pages[4].extract_text()
        assert "orbit-only.pdf" in pages[4].extract_text()

    def test_a_group_pack_is_every_candidate_in_turn(self):
        pdf, summary = AP.build_candidate_pack(
            [{"name": "A", "cover_letter": None}, {"name": "B", "cover_letter": "Hi"}],
            lambda fid: None,
        )
        pages = _pages(pdf)
        assert len(pages) == 2
        assert "No cover letter provided." in pages[0].extract_text()
        assert "Candidate pack — B" in pages[1].extract_text()

    def test_a_ceiling_on_pages(self, monkeypatch):
        monkeypatch.setattr(AP, "MAX_PAGES", 3)
        big = AP.text_pdf([("body", "x " * 20000)])
        pdf, summary = AP.build_candidate_pack(
            [{"name": "A", "documents": [{"file_name": "big.pdf", "file_id": "big"}]}],
            lambda fid: (big, "big.pdf", "application/pdf"),
        )
        assert summary["truncated"] is True and len(_pages(pdf)) <= 3


@pytest.fixture()
def world(db_session):
    org = _make_organization(db_session)
    user = _make_user(db_session, email=f"{uuid.uuid4().hex[:8]}@t.local")
    mem = _make_membership(db_session, user, org)
    role = Role(
        id=str(uuid.uuid4()),
        organization_id=org.id,
        name=f"r-{uuid.uuid4().hex[:6]}",
        display_name="r",
        permissions=["hr:read"],
    )
    db_session.add(role)
    db_session.flush()
    mem.role_id = role.id
    db_session.flush()
    app, version = bind(get_builtin("hiring"), org.id)
    door = AP.pdf_door(db_session, app, version, user)
    # The door is only ever called from the app's own logic (a run), which is
    # what lets it store the pack — built-ins refuse direct writes.
    from types import SimpleNamespace

    def candidate_pack(*args, **kwargs):
        return AR._run_in_logic(door.candidate_pack, *args, **kwargs)

    return org, user, SimpleNamespace(candidate_pack=candidate_pack)


def _file(db, org, data, name="cv.pdf", collection="candidate_documents"):
    f = AppFile(
        namespace="hr_suite",
        organization_id=org.id,
        collection=collection,
        record_id=str(uuid.uuid4()),
        filename=name,
        content_type="application/pdf",
        size_bytes=len(data),
        data=data,
    )
    db.add(f)
    db.flush()
    return f.id


class TestTheDoor:
    def test_builds_stores_and_replaces(self, db_session, world):
        org, user, door = world
        cv = _file(db_session, org, AP.text_pdf([("body", "MY CV")]))
        first = door.candidate_pack(
            [{"name": "Maia", "documents": [{"file_name": "cv.pdf", "file_id": cv}]}],
            filename="Candidate pack — Maia",
            collection="interviews",
            record_id="iv-1",
        )
        assert first["ok"] is True and first["filename"] == "Candidate pack — Maia.pdf"
        row = db_session.query(AppFile).filter(AppFile.id == first["file_id"]).one()
        assert (row.collection, row.record_id, row.organization_id) == (
            "interviews",
            "iv-1",
            org.id,
        )
        assert "MY CV" in _text(bytes(row.data))
        second = door.candidate_pack(
            [{"name": "Maia"}],
            collection="interviews",
            record_id="iv-1",
            replace_file_id=first["file_id"],
        )
        assert second["ok"] is True
        ids = [
            f.id for f in db_session.query(AppFile).filter(AppFile.record_id == "iv-1")
        ]
        assert ids == [second["file_id"]]

    def test_another_orgs_file_never_gets_in(self, db_session, world):
        org, user, door = world
        other = _make_organization(db_session)
        theirs = _file(db_session, other, AP.text_pdf([("body", "SECRET CV")]))
        out = door.candidate_pack(
            [{"name": "X", "documents": [{"file_name": "cv.pdf", "file_id": theirs}]}],
            collection="interviews",
            record_id="iv-2",
        )
        assert out["ok"] is True and out["included"] == []
        assert out["skipped"][0]["reason"] == "not available"
        row = db_session.query(AppFile).filter(AppFile.id == out["file_id"]).one()
        assert "SECRET" not in _text(bytes(row.data))

    def test_only_declared_collections_and_never_raises(self, db_session, world):
        _org, _user, door = world
        out = door.candidate_pack([{"name": "X"}], collection="salaries", record_id="r")
        assert (
            out["ok"] is False
            and "not a collection this app declares" in out["message"]
        )
        assert door.candidate_pack([], collection="interviews")["ok"] is False
        assert door.candidate_pack([{"name": "X"}])["ok"] is False

    def test_the_sandbox_gets_a_function_not_the_session(self, world):
        _org, _user, door = world
        assert set(vars(door)) == {"candidate_pack"}
