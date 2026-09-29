"""get_applicant_resume — the CV reader, a consolidator since Sep 2026.

It used to be a Norm built-in calling BambooHR's /files/{id} directly; under
the rule that anything reaching an outside system is a consolidator
(docs/tool-architecture-strategy.md) it now reads the application's CV file id
and downloads through the download_file endpoint. It hands the file to the
model as a document block (``_document``), which the tool loop lifts out of the
stored result. Exec'd under the REAL sandbox namespace.
"""

import pathlib

from app.connectors.function_executor import _SAFE_BUILTINS, _SAFE_MODULES

CODE = (
    pathlib.Path(__file__).resolve().parent.parent
    / "config"
    / "consolidators"
    / "get_applicant_resume.py"
).read_text()

PDF = {
    "content_base64": "JVBERi0xLjcK",
    "content_type": "application/pdf",
    "size_bytes": 9,
}


def _run(params, responses):
    ns = {"__builtins__": _SAFE_BUILTINS, **_SAFE_MODULES}
    exec(CODE, ns)
    calls = []

    def call_api(connector, action, body):
        calls.append((connector, action, body))
        return responses[action]

    return ns["run"](params, call_api, lambda m: None), calls


def test_an_application_id_is_enough():
    out, calls = _run(
        {"application_id": 42},
        {
            "get_application_details": {"id": 42, "resumeFileId": 777},
            "download_file": PDF,
        },
    )
    assert [c[1] for c in calls] == ["get_application_details", "download_file"]
    assert calls[1][2] == {"file_id": 777}
    doc = out["_document"]
    assert doc["type"] == "document"
    assert doc["source"] == {
        "type": "base64",
        "media_type": "application/pdf",
        "data": "JVBERi0xLjcK",
    }
    assert out["file_id"] == 777


def test_a_file_id_skips_the_lookup():
    out, calls = _run({"file_id": 5}, {"download_file": PDF})
    assert [c[1] for c in calls] == ["download_file"]
    assert out["_document"]["source"]["data"] == PDF["content_base64"]


def test_an_image_cv_is_an_image_block():
    img = {**PDF, "content_type": "image/png; charset=binary"}
    out, _ = _run({"file_id": 5}, {"download_file": img})
    assert out["_document"]["type"] == "image"
    assert out["_document"]["source"]["media_type"] == "image/png"


def test_an_application_without_a_cv_says_so():
    out, calls = _run({"application_id": 9}, {"get_application_details": {"id": 9}})
    assert "no CV" in out["error"]
    assert [c[1] for c in calls] == ["get_application_details"]


def test_a_failed_download_is_an_error_not_an_empty_document():
    out, _ = _run({"file_id": 5}, {"download_file": {"error": "404"}})
    assert "Could not download" in out["error"]
    assert "_document" not in out
