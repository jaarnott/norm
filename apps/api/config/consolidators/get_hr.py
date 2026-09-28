# ruff: noqa: F821 — sandbox-injected names; not imports.
#
# Canonical function_code for `bamboohr.get_hr` — THE BambooHR read tool
# (installed by scripts/sync_last_raw_tools.py, Sep 2026). Three views:
#
#   employees      current staff from the directory — name, job title,
#                  venue, work contact — filtered by name/role (query) or venue
#                  (location); employee_id -> one person's HR record.
#   jobs           roles and their applicant counts; open roles by default.
#   applications   candidates, newest first — for a job, a status or a name;
#                  application_id -> one application in full.
#
# It replaces seven raw reads on the agent menu, and fixes what they got wrong
# on first contact with the real account (28 Sep 2026):
#   - list_employees returned only its first page: 250 of 1,504 people, 245 of
#     them long gone. "Who works here" couldn't be answered. The directory is
#     the current staff (95) in one call, each with their venue.
#   - get_employee returned {"id": "5"} — BambooHR only returns fields you name,
#     and the row named none. get_employee_detail asks for them.
#   - get_jobs returned all 162 jobs (~47k tokens) for "what are we hiring
#     for?"; 12 are open.
#   - get_applications could only filter by job, one page of 50. The query row
#     passes status, name search and page.
#
# Reading a CV stays with get_applicant_resume — an internal handler, not a raw
# row: it fetches the file and hands the model the PDF itself as a document
# block, which a consolidator cannot return. The application view gives its
# file id.
#
# Requires consolidator_config: {"max_api_calls": 3}

_VIEWS = ("employees", "jobs", "applications")
#: BambooHR's applicationStatus groups (the query row passes these as-is).
_STATUS_GROUPS = {
    "all": "ALL",
    "active": "ALL_ACTIVE",
    "all_active": "ALL_ACTIVE",
    "new": "NEW",
    "inactive": "INACTIVE",
    "hired": "HIRED",
}
_JOB_STATUSES = ("open", "on hold", "filled", "draft", "canceled", "all")


def _lower(s):
    return str(s or "").strip().lower()


def _err(r):
    if isinstance(r, dict) and r.get("error"):
        return str(r["error"])
    return None


def _label(v):
    if isinstance(v, dict):
        return v.get("label") or v.get("name")
    return v


def _person(p):
    if not isinstance(p, dict):
        return None
    name = " ".join(x for x in (p.get("firstName"), p.get("lastName")) if x)
    return name or None


def _day(s):
    return str(s)[:10] if s else None


# ------------------------------------------------------------ employees ----

_DETAIL_KEEP = (
    "displayName",
    "firstName",
    "lastName",
    "preferredName",
    "jobTitle",
    "department",
    "location",
    "division",
    "status",
    "employmentHistoryStatus",
    "hireDate",
    "originalHireDate",
    "terminationDate",
    "workEmail",
    "mobilePhone",
    "supervisor",
)


def _employees(params, call_api):
    eid = params.get("employee_id")
    if eid:
        e = call_api("bamboohr", "get_employee_detail", {"employee_id": str(eid)})
        if not isinstance(e, dict) or _err(e):
            return {"error": _err(e) or f"employee {eid} not found"}
        out = {"view": "employees", "employee_id": str(eid)}
        for k in _DETAIL_KEEP:
            v = e.get(k)
            if v not in (None, "", "0000-00-00"):
                out[k] = v
        return out

    d = call_api("bamboohr", "get_employee_directory", {})
    staff = d.get("employees") if isinstance(d, dict) else None
    if not isinstance(staff, list):
        return {"error": _err(d) or "the BambooHR directory came back empty"}
    query = _lower(params.get("query"))
    loc = _lower(params.get("location"))
    rows = []
    for p in staff:
        if not isinstance(p, dict):
            continue
        name = p.get("displayName") or _person(p) or ""
        hay = " ".join(
            _lower(x) for x in (name, p.get("preferredName"), p.get("jobTitle"))
        )
        if query and query not in hay:
            continue
        if loc and loc not in _lower(p.get("location")):
            continue
        rows.append(
            {
                "id": p.get("id"),
                "name": name,
                "preferred": p.get("preferredName"),
                "job_title": p.get("jobTitle"),
                "venue": p.get("location"),
                "company": p.get("department"),
                "work_email": p.get("workEmail"),
                "mobile": p.get("mobilePhone"),
            }
        )
    rows.sort(key=lambda r: (_lower(r["venue"]), _lower(r["name"])))
    by_venue = {}
    for r in rows:
        key = r["venue"] or "—"
        by_venue[key] = by_venue.get(key, 0) + 1
    limit = int(params.get("limit") or 50)
    out = {
        "view": "employees",
        "current_staff": len(rows),
        "by_venue": by_venue,
        "employees": rows[:limit],
    }
    if len(rows) > limit:
        out["note"] = f"{len(rows) - limit} more — narrow with query or location"
    return out


# ----------------------------------------------------------------- jobs ----


def _jobs(params, call_api):
    jobs = call_api("bamboohr", "get_jobs", {})
    if not isinstance(jobs, list):
        return {"error": _err(jobs) or "BambooHR returned no jobs"}
    status = _lower(params.get("status")) or "open"
    if status not in _JOB_STATUSES:
        return {"error": f"job status must be one of {', '.join(_JOB_STATUSES)}"}
    query = _lower(params.get("query"))
    loc = _lower(params.get("location"))
    counts = {}
    rows = []
    for j in jobs:
        if not isinstance(j, dict):
            continue
        st = _label(j.get("status")) or ""
        counts[st] = counts.get(st, 0) + 1
        if status != "all" and _lower(st) != status:
            continue
        title = _label(j.get("title")) or ""
        location = j.get("location") or {}
        venue = (location.get("address") or {}).get("name") or location.get("label")
        if query and query not in _lower(title):
            continue
        if loc and loc not in _lower(venue) and loc not in _lower(title):
            continue
        rows.append(
            {
                "id": j.get("id"),
                "title": title,
                "venue": venue,
                "status": st,
                "posted": _day(j.get("postedDate")),
                "hiring_lead": _person(j.get("hiringLead")),
                "applicants": {
                    "total": j.get("totalApplicantsCount"),
                    "active": j.get("activeApplicantsCount"),
                    "new": j.get("newApplicantsCount"),
                },
            }
        )
    rows.sort(key=lambda r: r["posted"] or "", reverse=True)
    limit = int(params.get("limit") or 50)
    return {
        "view": "jobs",
        "status": status,
        "jobs": rows[:limit],
        "total": len(rows),
        "jobs_by_status": counts,
    }


# --------------------------------------------------------- applications ----


def _application(aid, call_api):
    d = call_api("bamboohr", "get_application_details", {"application_id": str(aid)})
    if not isinstance(d, dict) or _err(d):
        return {"error": _err(d) or f"application {aid} not found"}
    a = d.get("applicant") or {}
    job = d.get("job") or {}
    st = d.get("status") or {}
    qa = []
    for q in d.get("questionsAndAnswers") or []:
        if not isinstance(q, dict):
            continue
        question = _label(q.get("question"))
        answer = q.get("answer")
        if isinstance(answer, dict):
            answer = answer.get("label") or answer.get("value")
        qa.append({"q": str(question or "")[:200], "a": str(answer or "")[:300]})
    out = {
        "view": "applications",
        "id": d.get("id"),
        "applied": _day(d.get("appliedDate")),
        "status": st.get("label"),
        "status_changed": _day(st.get("dateChanged")),
        "rating": d.get("rating"),
        "applicant": {
            "name": _person(a),
            "email": a.get("email"),
            "phone": a.get("phoneNumber"),
            "source": a.get("source"),
            "available_from": a.get("availableStartDate"),
            "linkedin": a.get("linkedinUrl"),
        },
        "job": _label(job.get("title")),
        "hiring_lead": _person(job.get("hiringLead")),
        "desired_salary": d.get("desiredSalary"),
        "questions": qa,
        "counts": {
            "comments": d.get("commentCount"),
            "emails": d.get("emailCount"),
            "attachments": d.get("attachmentCount"),
            "also_considered_for": d.get("alsoConsideredForCount"),
        },
    }
    if d.get("resumeFileId"):
        out["resume"] = {
            "file_id": d.get("resumeFileId"),
            "note": "read the CV with get_applicant_resume(file_id)",
        }
    if d.get("coverLetterFileId"):
        out["cover_letter_file_id"] = d.get("coverLetterFileId")
    return out


def _status_filter(status, call_api):
    """({query params}, None) or (None, error message)."""
    group = _STATUS_GROUPS.get(status.replace(" ", "_"))
    if group:
        return {"status_group": group}, None
    statuses = call_api("bamboohr", "get_applicant_statuses", {})
    for s in statuses if isinstance(statuses, list) else []:
        if isinstance(s, dict) and status in (
            _lower(s.get("name")),
            _lower(s.get("translatedName")),
            _lower(s.get("code")),
        ):
            return {"status_id": str(s.get("id"))}, None
    names = sorted(
        s.get("name")
        for s in (statuses if isinstance(statuses, list) else [])
        if isinstance(s, dict) and s.get("enabled")
    )
    return None, (
        f"No application status '{status}'. Use active, new, hired, inactive or "
        "all, or one of: " + ", ".join(names)
    )


def _applications(params, call_api):
    aid = params.get("application_id")
    if aid:
        return _application(aid, call_api)

    q = {"page": int(params.get("page") or 1)}
    if params.get("job_id"):
        q["job_id"] = str(params["job_id"])
    status = _lower(params.get("status"))
    if not status and not params.get("job_id") and not params.get("query"):
        status = "active"
    if status:
        extra, serr = _status_filter(status, call_api)
        if serr:
            return {"error": serr}
        q.update(extra)
    if params.get("query"):
        q["search"] = str(params["query"])
    if not params.get("job_id"):
        # A name search looks across every job — the person may have applied
        # for a role that has since been filled.
        wide = status in ("hired", "inactive", "all") or (
            bool(params.get("query")) and not params.get("status")
        )
        q["job_status"] = "ALL" if wide else "DRAFT_AND_OPEN"

    page = call_api("bamboohr", "get_applications_query", q)
    apps = page.get("applications") if isinstance(page, dict) else None
    if not isinstance(apps, list):
        return {"error": _err(page) or "BambooHR returned no applications"}
    rows = [
        {
            "id": a.get("id"),
            "applied": _day(a.get("appliedDate")),
            "status": _label(a.get("status")),
            "name": _person(a.get("applicant")),
            "email": (a.get("applicant") or {}).get("email"),
            "job": _label((a.get("job") or {}).get("title")),
            "rating": a.get("rating"),
        }
        for a in apps
        if isinstance(a, dict)
    ]
    out = {
        "view": "applications",
        "filters": {k: v for k, v in q.items() if k != "page"},
        "page": q["page"],
        "applications": rows,
        "shown": len(rows),
        "more": not page.get("paginationComplete", True),
    }
    if out["more"]:
        out["note"] = f"more applications — ask for page {q['page'] + 1}"
    return out


# ------------------------------------------------------------------ run ----


def run(params, call_api, log):
    view = _lower(params.get("view"))
    if not view:
        if params.get("application_id") or params.get("job_id"):
            view = "applications"
        else:
            view = "employees"
    if view not in _VIEWS:
        return {"error": f"view must be one of {', '.join(_VIEWS)}"}
    if view == "employees":
        return _employees(params, call_api)
    if view == "jobs":
        return _jobs(params, call_api)
    return _applications(params, call_api)
