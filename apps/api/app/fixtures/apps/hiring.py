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

    return {"error": "unknown op '" + str(op) + "'"}
