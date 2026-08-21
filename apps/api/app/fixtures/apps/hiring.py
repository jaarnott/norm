"""Hiring — server-side logic.

Shares the ``hr_suite`` namespace with the Training app by invitation (Training
owns it and names this app in ``storage.shared_with``). That is not a
convenience: hiring someone has to put them where training can find them, and
the alternative — one app reaching into another's tables — is the thing the
platform refuses.

The rule that carries the most weight here is the pipeline state machine, and
it is worth stating plainly because it is easy to rebuild wrongly:

**Stages are data, status is derived.** A job's stages are ordered rows the
user defines and renames freely, so nothing may switch on a stage NAME. What is
fixed is each stage's ``stage_type`` — ``active``, ``hired`` or ``rejected`` —
and an application's ``status`` is recomputed from the stage it moves into,
every time. Orbit derives it in two places and they disagree: its UI clears
``hired_at``/``rejected_at`` when a candidate moves back to an active stage and
its API does not, so a rejected-then-revived candidate keeps a rejection date
forever. Here it is derived once, in ``_apply_stage``, and the stamps are
cleared on the way back.
"""

_META = ("id", "venue_id", "created_at", "updated_at")

#: The pipeline Orbit seeds on a new job. Stage TYPE is what drives status;
#: names are just labels the user renames.
_DEFAULT_STAGES = [
    ("Applied", "active"),
    ("Screening", "active"),
    ("Interview", "active"),
    ("Trial Shift", "active"),
    ("Offer", "active"),
    ("Hired", "hired"),
    ("Rejected", "rejected"),
]

#: The standard application form.
_STANDARD_FIELDS = [
    ("first_name", "First name", "text", True),
    ("last_name", "Last name", "text", True),
    ("email", "Email", "email", True),
    ("phone", "Phone", "phone", False),
    ("cv", "CV / resume", "file", False),
    ("cover_letter", "Cover letter", "textarea", False),
]


def _index(rows, key="id"):
    return {r.get(key): r for r in rows}


def _group(rows, key):
    out = {}
    for r in rows:
        out.setdefault(r.get(key), []).append(r)
    return out


def _writable(record):
    """A record's own fields, without the envelope storage adds — what to send
    back on an update so the metadata is not written into ``data``."""
    return {
        k: v
        for k, v in record.items()
        if k not in ("id", "venue_id", "created_at", "updated_at")
    }


def _candidate_name(candidate):
    parts = [candidate.get("first_name"), candidate.get("last_name")]
    return " ".join([p for p in parts if p]) or candidate.get("email") or "(no name)"


def _stages_for(job_id):
    return sorted(
        store.list("pipeline_stages", where={"job_id": job_id}, limit=100),
        key=lambda s: s.get("sort_index") or 0,
    )


def _log(application, action, summary, actor):
    store.put(
        "activity",
        {
            "application_id": application.get("id"),
            "candidate_id": application.get("candidate_id"),
            "activity_type": action,
            "summary": summary,
            "actor": actor,
            "at": _now(),
        },
    )


def _now():
    return datetime.datetime.now(datetime.timezone.utc).isoformat()


def _apply_stage(application, stage):
    """Derive an application's status from the stage it is moving into.

    The three stamps move together with the status so they can never disagree
    with it — including clearing them when a candidate comes BACK to an active
    stage, which is the half Orbit's API forgets.
    """
    stage_type = stage.get("stage_type") or "active"
    next_state = dict(_writable(application))
    next_state["stage_id"] = stage.get("id")
    next_state["stage_changed_at"] = _now()
    if stage_type == "hired":
        next_state["status"] = "hired"
        next_state["hired_at"] = _now()
        next_state["rejected_at"] = None
    elif stage_type == "rejected":
        next_state["status"] = "rejected"
        next_state["rejected_at"] = _now()
        next_state["hired_at"] = None
    else:
        next_state["status"] = "active"
        next_state["hired_at"] = None
        next_state["rejected_at"] = None
    return next_state


def _recount_fill(job_id):
    """Keep ``job_openings.positions_filled`` equal to the number of hired
    applications. DERIVED from the pipeline, never incremented, so it cannot
    drift: the header showed 0/N forever because nothing maintained it."""
    apps = store.list("applications", where={"job_id": job_id}, limit=2000)
    filled = sum(1 for a in apps if a.get("status") == "hired")
    job = store.get("job_openings", job_id)
    if (job.get("positions_filled") or 0) != filled:
        data = {k: v for k, v in job.items() if k not in _META}
        data["positions_filled"] = filled
        store.put("job_openings", data, job["id"])


def _knockout_hit(job_id, answers):
    """Does any answer trip a knockout question? A field with
    ``knockout_enabled`` lists ``knockout_values``; an answer equal to one of
    them auto-flags the application — Orbit's right_to_work='No' rule, which the
    port carried as data but never evaluated."""
    fields = {
        f.get("field_key"): f
        for f in store.list("application_fields", where={"job_id": job_id}, limit=200)
    }
    for ans in answers or []:
        field = fields.get(ans.get("field_key"))
        if field and field.get("knockout_enabled"):
            if ans.get("value") in (field.get("knockout_values") or []):
                return True
    return False


def run(params, call_api, log):
    op = (params or {}).get("op") or "board"
    actor = (params or {}).get("actor") or "Norm"

    if op == "board":
        jobs = store.list("job_openings", limit=200)
        applications = store.list("applications", limit=2000)
        by_job = _group(applications, "job_id")
        rows = []
        for j in jobs:
            apps = by_job.get(j["id"], [])
            rows.append(
                {
                    **j,
                    "application_count": len(apps),
                    "active_count": sum(
                        1 for a in apps if (a.get("status") or "active") == "active"
                    ),
                    "hired_count": sum(1 for a in apps if a.get("status") == "hired"),
                }
            )
        rows.sort(key=lambda r: (r.get("status") != "open", str(r.get("title") or "")))
        return {"jobs": rows}

    if op == "pipeline":
        job_id = params.get("job_id")
        job = store.get("job_openings", job_id)
        stages = _stages_for(job_id)
        applications = store.list("applications", where={"job_id": job_id}, limit=1000)
        candidates = _index(store.list("candidates", limit=2000))
        by_stage = {}
        for a in applications:
            candidate = candidates.get(a.get("candidate_id")) or {}
            by_stage.setdefault(a.get("stage_id"), []).append(
                {
                    **a,
                    "candidate_name": _candidate_name(candidate),
                    "candidate_email": candidate.get("email"),
                    "source": candidate.get("source"),
                }
            )
        return {
            "job": job,
            "stages": [
                {**s, "applications": by_stage.get(s["id"], [])} for s in stages
            ],
        }

    if op == "candidate":
        application_id = params.get("application_id")
        application = store.get("applications", application_id)
        candidate = store.get("candidates", application.get("candidate_id"))
        answers = store.list(
            "answers", where={"application_id": application_id}, limit=200
        )
        notes = store.list("notes", where={"application_id": application_id}, limit=200)
        activity = store.list(
            "activity", where={"application_id": application_id}, limit=200
        )
        interviews = store.list(
            "interviews", where={"application_id": application_id}, limit=100
        )
        activity.sort(key=lambda r: str(r.get("at") or ""), reverse=True)
        notes.sort(key=lambda r: str(r.get("at") or ""), reverse=True)
        return {
            "application": application,
            "candidate": {**candidate, "name": _candidate_name(candidate)},
            "answers": sorted(answers, key=lambda r: r.get("sort_index") or 0),
            "notes": notes,
            "activity": activity,
            "interviews": interviews,
            "stages": _stages_for(application.get("job_id")),
        }

    if op == "move":
        application = store.get("applications", params.get("application_id"))
        stage = store.get("pipeline_stages", params.get("stage_id"))
        if stage.get("job_id") != application.get("job_id"):
            # Stages belong to a job. Without this a candidate could be moved
            # into another job's pipeline and vanish from both boards.
            return {"error": "that stage belongs to a different job"}
        updated = _apply_stage(application, stage)
        store.put("applications", updated, application["id"])
        _recount_fill(application.get("job_id"))
        _log(
            application,
            "stage_change",
            "Moved to " + str(stage.get("name") or "a new stage"),
            actor,
        )
        return {"ok": True, "status": updated["status"]}

    if op == "note":
        application = store.get("applications", params.get("application_id"))
        body = str(params.get("body") or "").strip()
        if not body:
            return {"error": "a note needs some text"}
        store.put(
            "notes",
            {
                "application_id": application["id"],
                "candidate_id": application.get("candidate_id"),
                "body": body,
                "author": actor,
                "at": _now(),
            },
        )
        _log(application, "note_added", "Note added", actor)
        return {"ok": True}

    if op == "hire":
        """Hiring is where the two apps meet.

        Moving to the hired stage is only half of it: the person has to exist
        in `people` for the Training app to enrol them, and that record lives
        in the namespace both apps share. Doing it here — rather than leaving
        someone to re-key a name into Training — is the whole reason these two
        apps share storage instead of owning separate copies of the same staff.
        """
        application = store.get("applications", params.get("application_id"))
        candidate = store.get("candidates", application.get("candidate_id"))
        stages = _stages_for(application.get("job_id"))
        # The FIRST hired stage in the user's own order. The loop used to keep
        # overwriting, so a job with two hired stages landed the candidate in
        # the last one.
        hired_stage = None
        for s in stages:
            if s.get("stage_type") == "hired":
                hired_stage = s
                break
        if not hired_stage:
            return {"error": "this job has no 'hired' stage to move them into"}

        # Idempotent: hiring twice must not mint a second person record.
        person_id = application.get("person_id")
        if not person_id:
            email = candidate.get("email")
            existing = (
                store.list("people", where={"email": email}, limit=5) if email else []
            )
            if existing:
                person_id = existing[0]["id"]
            else:
                person = store.put(
                    "people",
                    {
                        "name": _candidate_name(candidate),
                        "email": email,
                        "phone": candidate.get("phone"),
                        "role": params.get("role") or "team_member",
                        "is_active": True,
                        "hired_from_application_id": application["id"],
                        "start_date": params.get("start_date"),
                    },
                    venue_id=params.get("venue_id"),
                )
                person_id = person["id"]

        updated = _apply_stage(application, hired_stage)
        updated["person_id"] = person_id
        updated["hire_start_date"] = params.get("start_date")
        store.put("applications", updated, application["id"])
        _recount_fill(application.get("job_id"))
        _log(
            application,
            "hired",
            _candidate_name(candidate) + " hired — added to the team",
            actor,
        )
        return {"ok": True, "person_id": person_id}

    if op == "talent_pool":
        candidates = [
            c for c in store.list("candidates", limit=2000) if c.get("in_talent_pool")
        ]
        for c in candidates:
            c["name"] = _candidate_name(c)
        candidates.sort(key=lambda c: str(c.get("name")))
        return {"candidates": candidates}

    if op == "create_job":
        """Create a job, and seed the pipeline + application form Orbit gives
        every new job — a job with no stages cannot receive anyone."""
        fields = params.get("fields") or {}
        title = str(fields.get("title") or "").strip()
        if not title:
            return {"error": "a job needs a title"}
        job = store.put(
            "job_openings",
            {
                "title": title,
                "department": fields.get("department"),
                "employment_type": fields.get("employment_type") or "part_time",
                "status": "open",
                "is_published": False,
                "positions_to_fill": fields.get("positions_to_fill") or 1,
                "positions_filled": 0,
                "description_html": fields.get("description_html"),
                "created_at": _now(),
            },
            venue_id=params.get("venue_id") or None,
        )
        for i, (name, kind) in enumerate(_DEFAULT_STAGES):
            store.put(
                "pipeline_stages",
                {
                    "job_id": job["id"],
                    "name": name,
                    "stage_type": kind,
                    "sort_index": i,
                },
            )
        for i, (key, label, ftype, required) in enumerate(_STANDARD_FIELDS):
            store.put(
                "application_fields",
                {
                    "job_id": job["id"],
                    "field_key": key,
                    "label": label,
                    "field_type": ftype,
                    "is_required": required,
                    "is_enabled": True,
                    "is_standard": True,
                    "sort_index": i,
                },
            )
        return {"job": job}

    if op == "update_job":
        job_id = params.get("job_id")
        current = store.get("job_openings", job_id)
        data = {k: v for k, v in current.items() if k not in _META}
        for key in (
            "title",
            "department",
            "employment_type",
            "positions_to_fill",
            "description_html",
            "requirements_html",
            "pay_type",
            "pay_min",
            "pay_max",
        ):
            if key in (params.get("fields") or {}):
                data[key] = params["fields"][key]
        return {"job": store.put("job_openings", data, job_id)}

    if op == "set_job_status":
        """Publish / unpublish / close / reopen — status and is_published are
        orthogonal, so both are settable."""
        job_id = params.get("job_id")
        current = store.get("job_openings", job_id)
        data = {k: v for k, v in current.items() if k not in _META}
        if "status" in params:
            data["status"] = params["status"]
            if params["status"] == "closed":
                data["closed_at"] = _now()
        if "is_published" in params:
            data["is_published"] = bool(params["is_published"])
            if params["is_published"] and not data.get("published_at"):
                data["published_at"] = _now()
        return {"job": store.put("job_openings", data, job_id)}

    if op == "job_editor":
        """A job with its stages and application fields, for editing."""
        job_id = params.get("job_id")
        return {
            "job": store.get("job_openings", job_id),
            "stages": _stages_for(job_id),
            "fields": sorted(
                store.list("application_fields", where={"job_id": job_id}),
                key=lambda f: f.get("sort_index") or 0,
            ),
        }

    if op == "add_stage":
        job_id = params.get("job_id")
        existing = _stages_for(job_id)
        return {
            "stage": store.put(
                "pipeline_stages",
                {
                    "job_id": job_id,
                    "name": params.get("name") or "New stage",
                    "stage_type": params.get("stage_type") or "active",
                    "sort_index": len(existing),
                },
            )
        }

    if op == "update_stage":
        stage_id = params.get("stage_id")
        current = store.get("pipeline_stages", stage_id)
        data = {k: v for k, v in current.items() if k not in _META}
        for key in ("name", "stage_type"):
            if key in (params.get("fields") or {}):
                data[key] = params["fields"][key]
        return {"stage": store.put("pipeline_stages", data, stage_id)}

    if op == "delete_stage":
        stage_id = params.get("stage_id")
        stage = store.get("pipeline_stages", stage_id)
        # Refuse to strand applicants: a stage with people on it cannot go.
        on_it = [
            a
            for a in store.list("applications", where={"job_id": stage.get("job_id")})
            if a.get("stage_id") == stage_id
        ]
        if on_it:
            return {
                "error": f"{len(on_it)} candidate(s) are on that stage — move them first"
            }
        store.delete("pipeline_stages", stage_id)
        return {"ok": True}

    if op == "reorder_stages":
        for index, sid in enumerate(params.get("ids") or []):
            current = store.get("pipeline_stages", sid)
            data = {k: v for k, v in current.items() if k not in _META}
            data["sort_index"] = index
            store.put("pipeline_stages", data, sid)
        return {"ok": True}

    if op == "add_field":
        job_id = params.get("job_id")
        existing = store.list("application_fields", where={"job_id": job_id})
        data = {
            "job_id": job_id,
            "field_key": params.get("field_key") or "field",
            "label": params.get("label") or "Field",
            "field_type": params.get("field_type") or "text",
            "is_required": bool(params.get("is_required")),
            "is_enabled": True,
            "is_standard": False,
            "sort_index": len(existing),
            "knockout_enabled": bool(params.get("knockout_enabled")),
        }
        # A select field carries its options; a knockout question carries the
        # values that reject. Both are optional and only kept when supplied.
        for key in ("help_text", "options", "knockout_values"):
            if params.get(key) is not None:
                data[key] = params.get(key)
        return {"field": store.put("application_fields", data)}

    if op == "update_field":
        """Edit a field — including turning knockout on for a standard field
        like right_to_work, which the migration carries but nothing could set."""
        field_id = params.get("field_id")
        current = store.get("application_fields", field_id)
        data = {k: v for k, v in current.items() if k not in _META}
        for key in (
            "label",
            "field_type",
            "is_required",
            "is_enabled",
            "help_text",
            "options",
            "knockout_enabled",
            "knockout_values",
        ):
            if key in (params.get("fields") or {}):
                data[key] = params["fields"][key]
        return {"field": store.put("application_fields", data, field_id)}

    if op == "delete_field":
        store.delete("application_fields", params.get("field_id"))
        return {"ok": True}

    if op == "add_candidate":
        """Create a candidate and, if a job is named, an application on its
        first stage. This is how someone gets into the pipeline by hand."""
        first = str(params.get("first_name") or "").strip()
        last = str(params.get("last_name") or "").strip()
        email = str(params.get("email") or "").strip() or None
        if not (first or last or email):
            return {"error": "a name or an email is required"}
        # Reuse an existing candidate by email rather than duplicating.
        candidate_id = None
        if email:
            match = store.list("candidates", where={"email": email}, limit=1)
            if match:
                candidate_id = match[0]["id"]
        if not candidate_id:
            candidate_id = store.put(
                "candidates",
                {
                    "first_name": first,
                    "last_name": last,
                    "email": email,
                    "phone": params.get("phone"),
                    "source": params.get("source") or "manual",
                    "in_talent_pool": False,
                },
            )["id"]
        job_id = params.get("job_id")
        if not job_id:
            return {"ok": True, "candidate_id": candidate_id}
        stages = _stages_for(job_id)
        first_stage = stages[0]["id"] if stages else None
        answers = params.get("answers") or []
        app_data = {
            "job_id": job_id,
            "candidate_id": candidate_id,
            "stage_id": first_stage,
            "status": "active",
            "source": params.get("source") or "manual",
            "applied_at": _now(),
        }
        # Evaluate knockout questions against the answers given, if any — this
        # is what makes the pipeline's knockout pill mean something.
        if _knockout_hit(job_id, answers):
            app_data["knockout_flag"] = True
        application = store.put(
            "applications", app_data, venue_id=params.get("venue_id") or None
        )
        for ans in answers:
            if ans.get("field_key"):
                store.put(
                    "answers",
                    {
                        "application_id": application["id"],
                        "field_key": ans.get("field_key"),
                        "label": ans.get("label"),
                        "value": ans.get("value"),
                    },
                )
        _log(application, "application_created", "Added to the pipeline", actor)
        return {
            "ok": True,
            "application_id": application["id"],
            "knockout": bool(app_data.get("knockout_flag")),
        }

    if op == "reject":
        """Move to a rejected stage with a reason recorded."""
        application = store.get("applications", params.get("application_id"))
        stages = _stages_for(application.get("job_id"))
        rejected = None
        for s in stages:
            if s.get("stage_type") == "rejected":
                rejected = s
                break
        if not rejected:
            return {"error": "this job has no 'rejected' stage"}
        updated = _apply_stage(application, rejected)
        updated["rejection_reason"] = params.get("reason")
        store.put("applications", updated, application["id"])
        _recount_fill(application.get("job_id"))
        _log(
            application,
            "rejected",
            "Not progressing"
            + (": " + params["reason"] if params.get("reason") else ""),
            actor,
        )
        return {"ok": True}

    if op == "set_rating":
        application = store.get("applications", params.get("application_id"))
        data = {k: v for k, v in application.items() if k not in _META}
        data["rating"] = params.get("rating")
        return {"application": store.put("applications", data, application["id"])}

    if op == "set_talent_pool":
        candidate = store.get("candidates", params.get("candidate_id"))
        data = {k: v for k, v in candidate.items() if k not in _META}
        data["in_talent_pool"] = bool(params.get("in_talent_pool"))
        if params.get("note") is not None:
            data["talent_pool_note"] = params.get("note")
        return {"candidate": store.put("candidates", data, candidate["id"])}

    if op == "schedule_interview":
        """Record an interview. No calendar integration here — that is a
        connector concern; this stores the intent so the pipeline shows it.
        `scheduled_at` carries a time, not just a date, and the interviewers,
        meeting link and briefing ride along (all carried by the migration but
        previously unsettable)."""
        application = store.get("applications", params.get("application_id"))
        data = {
            "application_id": application["id"],
            "candidate_id": application.get("candidate_id"),
            "job_id": application.get("job_id"),
            "interview_type": params.get("interview_type") or "in_person",
            "scheduled_at": params.get("scheduled_at"),
            "location": params.get("location"),
            "status": "scheduled",
        }
        for key in ("duration_minutes", "meeting_url", "instructions"):
            if params.get(key):
                data[key] = params.get(key)
        if params.get("interviewers"):
            data["interviewers"] = params.get("interviewers")
        interview = store.put("interviews", data)
        _log(application, "interview_scheduled", "Interview scheduled", actor)
        return {"interview": interview}

    return {"error": "unknown op '" + str(op) + "'"}
