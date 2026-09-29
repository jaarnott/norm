# ruff: noqa: F821 — sandbox-injected names; not imports.
#
# Canonical function_code for `bamboohr.get_applicant_resume` — read an
# applicant's CV (installed by scripts/sync_cv_reader_consolidator.py, Sep 2026).
#
# Until Sep 2026 this was a Norm built-in that called BambooHR's /files/{id}
# directly with the API key. The rule (docs/tool-architecture-strategy.md) is
# that anything reaching an outside system is a consolidator over endpoints,
# so it now downloads through the `download_file` endpoint and hands the file
# to the model as a document block (`_document`, which the tool loop lifts out
# of the stored result and attaches to the tool result).
#
# It takes the application itself — application_id — so the agent no longer
# needs a get_hr call first just to learn the CV's file id. file_id still works.
#
# Requires consolidator_config: {"max_api_calls": 2}


def run(params, call_api, log):
    file_id = params.get("file_id") or params.get("resume_file_id")
    application_id = params.get("application_id")
    if not file_id and application_id:
        detail = call_api(
            "bamboohr", "get_application_details", {"application_id": application_id}
        )
        if isinstance(detail, dict) and detail.get("error"):
            return {"error": f"Could not read application {application_id}: {detail['error']}"}
        file_id = (detail or {}).get("resumeFileId") if isinstance(detail, dict) else None
        if not file_id:
            return {"error": f"Application {application_id} has no CV on file."}
    if not file_id:
        return {"error": "Pass application_id (or the CV's file_id)."}

    f = call_api("bamboohr", "download_file", {"file_id": file_id})
    if not isinstance(f, dict) or not f.get("content_base64"):
        err = f.get("error") if isinstance(f, dict) else None
        return {"error": f"Could not download file {file_id}" + (f": {err}" if err else "")}

    media = (f.get("content_type") or "application/pdf").split(";")[0].strip()
    log(f"CV {file_id}: {media}, {f.get('size_bytes')} bytes")
    return {
        "file_id": file_id,
        "application_id": application_id,
        "content_type": media,
        "size_bytes": f.get("size_bytes"),
        "_document": {
            "type": "image" if media.startswith("image/") else "document",
            "source": {"type": "base64", "media_type": media, "data": f["content_base64"]},
        },
    }
