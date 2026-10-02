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
# And, from the consolidator review (1 Oct 2026, fixed 2 Oct):
#   - venue names: Norm says "The Glass Goose", BambooHR "Glass Goose", and
#     "Mr Murdoch's" vs "Mr Murdochs" — a plain substring check found 0 staff.
#     Names now compare after dropping case, punctuation and a leading "The"
#     ("&" reads as "and");
#     still a whole-name match, never a partial one (similar names must not
#     collide). No match lists BambooHR's own venue names.
#   - a BambooHR failure was read as an answer: a failed statuses lookup said
#     "no such status", and the 28 Sep 401 made the agent fall back to another
#     app without saying so. Every failure now says it is BambooHR's, and a
#     refused key says an admin must re-enter it.
#   - "kitchen" found 1 person: the directory's division (Kitchen, Front of
#     House, Management) is now searched and shown.
#   - a job's candidates are BambooHR's ACTIVE ones unless asked (Head Chef: 5
#     of 50); that default is now explicit in `filters`.
#   - rows carry job_id; limit is capped at 100.
#
# The CV reader folded in (2 Oct 2026): application_id with cv true returns
# the application AND the CV itself as a document block (`_document`, which
# the tool loop lifts out of the stored result and attaches for the model —
# a Word CV is converted to text there). It was the separate
# get_applicant_resume tool, which cost a second tool call per candidate.
#
# BambooHR is the group's hiring system (Norm Hiring is switched off).
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
_MAX_LIMIT = 100


def _lower(s):
    return str(s or "").strip().lower()


def _err(r):
    if isinstance(r, dict) and r.get("error"):
        return str(r["error"])
    return None


def _plain(text):
    """An error message without the HTML page some failures carry."""
    out, in_tag = [], False
    for ch in str(text or ""):
        if ch == "<":
            in_tag = True
        elif ch == ">":
            in_tag = False
            out.append(" ")
        elif not in_tag:
            out.append(ch)
    return " ".join("".join(out).split())[:200]


def _failed(what, r):
    """The error for a BambooHR read that failed — never an empty answer."""
    e = _err(r) or "an unexpected response"
    if "401" in e or "403" in e:
        return (
            f"BambooHR refused Norm's API key reading {what} ({_plain(e)}). An admin "
            "needs to re-enter the BambooHR key in Norm's connection settings. Tell "
            "the user that — don't answer from another system instead."
        )
    return (
        f"Couldn't read {what} from BambooHR ({_plain(e)}) — this is a BambooHR "
        "failure, not missing data; try again."
    )


def _venue_key(name):
    """'The Glass Goose' and 'Glass Goose', "Mr Murdoch's" and 'Mr Murdochs'
    compare equal: case, punctuation and a leading 'The' are dropped. A whole
    name still has to match — 'Bidfood Fresh' never matches 'Bidfood'."""
    kept = "".join(
        ch if ch.isalnum() else (" and " if ch == "&" else " " if ch in " -/,." else "")
        for ch in str(name or "").lower()
    )
    words = kept.split()
    if words and words[0] == "the":
        words = words[1:]
    return " ".join(words)


def _int(v, default):
    try:
        return int(v)
    except (TypeError, ValueError):
        return default


def _limit(params, default=50):
    return max(1, min(_int(params.get("limit"), default), _MAX_LIMIT))


def _flag(value):
    if isinstance(value, str):
        return value.strip().lower() in ("true", "yes", "1")
    return bool(value)


def _no_venue(loc, names):
    names = sorted(n for n in names if n)
    return f"No venue called '{loc}' in BambooHR. Its venues: " + ", ".join(names) + "."


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
        if _err(e) and "404" in _err(e):
            return {"error": f"No employee {eid} in BambooHR."}
        if not isinstance(e, dict) or _err(e):
            return {"error": _failed(f"employee {eid}", e)}
        out = {"view": "employees", "employee_id": str(eid)}
        for k in _DETAIL_KEEP:
            v = e.get(k)
            if v not in (None, "", "0000-00-00"):
                out[k] = v
        return out

    d = call_api("bamboohr", "get_employee_directory", {})
    staff = d.get("employees") if isinstance(d, dict) else None
    if not isinstance(staff, list):
        return {"error": _failed("the staff directory", d)}
    query = _lower(params.get("query"))
    loc = _venue_key(params.get("location"))
    rows = []
    for p in staff:
        if not isinstance(p, dict):
            continue
        name = p.get("displayName") or _person(p) or ""
        hay = " ".join(
            _lower(x)
            for x in (
                name,
                p.get("preferredName"),
                p.get("jobTitle"),
                p.get("division"),
            )
        )
        if query and query not in hay:
            continue
        if loc and loc != _venue_key(p.get("location")):
            continue
        rows.append(
            {
                "id": p.get("id"),
                "name": name,
                "preferred": p.get("preferredName"),
                "job_title": p.get("jobTitle"),
                "venue": p.get("location"),
                "division": p.get("division"),
                "company": p.get("department"),
                "work_email": p.get("workEmail"),
                "mobile": p.get("mobilePhone"),
            }
        )
    if loc and not rows:
        return {
            "view": "employees",
            "current_staff": 0,
            "employees": [],
            "note": _no_venue(
                params.get("location"),
                {p.get("location") for p in staff if isinstance(p, dict)},
            ),
        }
    rows.sort(key=lambda r: (_lower(r["venue"]), _lower(r["name"])))
    by_venue = {}
    for r in rows:
        key = r["venue"] or "—"
        by_venue[key] = by_venue.get(key, 0) + 1
    limit = _limit(params)
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


def _job_venue(j):
    location = j.get("location") or {}
    return (location.get("address") or {}).get("name") or location.get("label")


def _jobs(params, call_api):
    status = _lower(params.get("status")) or "open"
    if status not in _JOB_STATUSES:
        return {"error": f"job status must be one of {', '.join(_JOB_STATUSES)}"}
    jobs = call_api("bamboohr", "get_jobs", {})
    if not isinstance(jobs, list):
        return {"error": _failed("jobs", jobs)}
    query = _lower(params.get("query"))
    loc = _venue_key(params.get("location"))
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
        venue = _job_venue(j)
        if query and query not in _lower(title):
            continue
        # A job's venue is its location, or the whole venue name leading its
        # title ("Mr Murdoch's - Bar Team - Part Time").
        if (
            loc
            and loc != _venue_key(venue)
            and not (_venue_key(title) + " ").startswith(loc + " ")
        ):
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
    if (
        loc
        and not rows
        and not any(
            loc == _venue_key(_job_venue(j)) for j in jobs if isinstance(j, dict)
        )
    ):
        return {
            "view": "jobs",
            "status": status,
            "jobs": [],
            "total": 0,
            "note": _no_venue(
                params.get("location"),
                {_job_venue(j) for j in jobs if isinstance(j, dict)},
            ),
        }
    limit = _limit(params)
    return {
        "view": "jobs",
        "status": status,
        "jobs": rows[:limit],
        "total": len(rows),
        "jobs_by_status": counts,
    }


# --------------------------------------------------------- applications ----


def _application(aid, call_api, log, cv=False):
    d = call_api("bamboohr", "get_application_details", {"application_id": str(aid)})
    if _err(d) and "404" in _err(d):
        return {"error": f"No application {aid} in BambooHR."}
    if not isinstance(d, dict) or _err(d):
        return {"error": _failed(f"application {aid}", d)}
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
        "job_id": job.get("id"),
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
    if d.get("coverLetterFileId"):
        out["cover_letter_file_id"] = d.get("coverLetterFileId")
    file_id = d.get("resumeFileId")
    if not cv:
        out["cv"] = (
            "on file — pass cv true with this application_id to read it"
            if file_id
            else "none on file"
        )
        return out
    if not file_id:
        out["cv"] = "none on file"
        return out
    f = call_api("bamboohr", "download_file", {"file_id": str(file_id)})
    if not isinstance(f, dict) or not f.get("content_base64"):
        out["cv"] = _failed(f"the CV (file {file_id})", f)
        return out
    media = (f.get("content_type") or "application/pdf").split(";")[0].strip()
    log(f"CV {file_id}: {media}, {f.get('size_bytes')} bytes")
    out["cv"] = {
        "file_id": file_id,
        "content_type": media,
        "size_bytes": f.get("size_bytes"),
    }
    out["_document"] = {
        "type": "image" if media.startswith("image/") else "document",
        "source": {"type": "base64", "media_type": media, "data": f["content_base64"]},
    }
    return out


def _status_filter(status, call_api):
    """({query params}, None) or (None, error message)."""
    group = _STATUS_GROUPS.get(status.replace(" ", "_"))
    if group:
        return {"status_group": group}, None
    statuses = call_api("bamboohr", "get_applicant_statuses", {})
    if not isinstance(statuses, list):
        return None, _failed("the application statuses", statuses)
    for s in statuses:
        if isinstance(s, dict) and status in (
            _lower(s.get("name")),
            _lower(s.get("translatedName")),
            _lower(s.get("code")),
        ):
            return {"status_id": str(s.get("id"))}, None
    names = sorted(
        s.get("name") for s in statuses if isinstance(s, dict) and s.get("enabled")
    )
    return None, (
        f"No application status '{status}'. Use active, new, hired, inactive or "
        "all, or one of: " + ", ".join(names)
    )


def _applications(params, call_api, log):
    aid = params.get("application_id")
    if aid:
        return _application(aid, call_api, log, cv=_flag(params.get("cv")))
    if _flag(params.get("cv")):
        return {"error": "cv needs an application_id — the CV of one application."}

    q = {"page": max(1, _int(params.get("page"), 1))}
    if params.get("job_id"):
        q["job_id"] = str(params["job_id"])
    status = _lower(params.get("status"))
    # BambooHR's own default is active candidates only — said out loud here,
    # so `filters` shows it (a job read 5 of its 50 candidates without saying).
    defaulted = not status and not params.get("query")
    if defaulted:
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
        return {"error": _failed("applications", page)}
    rows = [
        {
            "id": a.get("id"),
            "applied": _day(a.get("appliedDate")),
            "status": _label(a.get("status")),
            "name": _person(a.get("applicant")),
            "email": (a.get("applicant") or {}).get("email"),
            "job": _label((a.get("job") or {}).get("title")),
            "job_id": (a.get("job") or {}).get("id"),
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
    notes = []
    if defaulted:
        notes.append("active candidates only — status 'all' for everyone")
    if out["more"]:
        notes.append(f"more applications — ask for page {q['page'] + 1}")
    if notes:
        out["note"] = "; ".join(notes)
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
    return _applications(params, call_api, log)
