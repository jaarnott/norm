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

Interviews (Oct 2026, from Orbit): a single interview or a GROUP interview
(one calendar event, every candidate keeping their own interview row for
feedback). Both send their invite from the job venue's mailbox through the
platform's ``calendar`` door, with a candidate pack from the ``pdf`` door, and
the candidate gets an email through ``email`` — on create and reschedule only
(Orbit re-sends it on every save), made idempotent by its key.

Who did something is the platform's to say (``params["_viewer"]``), never the
page's: the old ``actor: 'Norm'`` a page sent is ignored.
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

_INTERVIEW_TYPES = {
    "phone_screen": "Phone screen",
    "in_person": "In person",
    "video": "Video call",
    "trial_shift": "Trial shift",
    "panel": "Panel",
}

#: Fields a group interview shares with every one of its members.
_GROUP_SHARED = (
    "interview_type",
    "scheduled_at",
    "duration_minutes",
    "location",
    "meeting_url",
    "instructions",
    "scorecard_template_id",
    "invite_candidate",
    "interviewer_ids",
)

#: Never copied onto a duplicate: identity, lifecycle and the import's keys
#: (a copy carrying an orbit_id would be "corrected" by the next refresh).
_NOT_COPIED = (
    "slug",
    "status",
    "is_published",
    "published_at",
    "closed_at",
    "positions_filled",
    "orbit_id",
    "created_at",
    "updated_at",
    "created_by",
    "created_by_user_id",
)


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


def _best_match(rows, want, label):
    """The row ``want`` names — its id, else its label: exact, then prefix,
    then anywhere, ignoring case. Norm opens the app by what people call
    things ("the chef role", "Sam"), never by our ids."""
    want = str(want or "").strip().lower()
    if not want:
        return None
    for r in rows:
        if str(r.get("id") or "").lower() == want:
            return r
    named = [(str(label(r) or "").lower(), r) for r in rows]
    for test in (
        lambda n: n == want,
        lambda n: n.startswith(want),
        lambda n: want in n,
    ):
        hits = [r for n, r in named if n and test(n)]
        if hits:
            return hits[0]
    return None


def _stages_for(job_id):
    return sorted(
        store.list("pipeline_stages", where={"job_id": job_id}, limit=100),
        key=lambda s: s.get("sort_index") or 0,
    )


def _now():
    return datetime.datetime.now(datetime.timezone.utc).isoformat()


def _esc(value):
    return (
        str(value if value is not None else "")
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
    )


def _ts(value):
    """An ISO timestamp as an aware datetime (naive read as UTC), or None."""
    if not value:
        return None
    try:
        d = datetime.datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except Exception:
        return None
    if d.tzinfo is None:
        d = d.replace(tzinfo=datetime.timezone.utc)
    return d


def _iso(value):
    """A time to store: aware values normalised to UTC ISO; anything else as
    given (so nothing a caller sends is silently lost)."""
    if not value:
        return None
    try:
        d = datetime.datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except Exception:
        return str(value)
    if d.tzinfo is None:
        return str(value)
    return d.astimezone(datetime.timezone.utc).isoformat()


def _moved(before, after):
    """Did the interview's time change? To the minute: the page's date-time
    field has no seconds, so an imported 10:00:30 must not read as moved
    (and re-send every invitation) just because it was opened and saved."""
    a, b = _ts(before), _ts(after)
    if a is None or b is None:
        return (a is None) != (b is None)
    return a.replace(second=0, microsecond=0) != b.replace(second=0, microsecond=0)


def _door(name):
    """The platform door called ``name``, or None when this run has none."""
    try:
        if name == "calendar":
            return calendar
        if name == "pdf":
            return pdf
        if name == "email":
            return email
    except Exception:
        return None
    return None


def _actor(params):
    """Who is acting — from the platform's ``_viewer``. Their person record
    (``people.norm_user_id``) when they have one, for ``created_by``."""
    viewer = (params or {}).get("_viewer") or {}
    user_id = viewer.get("user_id")
    person_id = None
    if user_id:
        hit = store.list("people", where={"norm_user_id": user_id}, limit=1)
        if hit:
            person_id = hit[0]["id"]
    return {
        "name": viewer.get("name") or ("Norm" if viewer.get("system") else "Someone"),
        "user_id": user_id,
        "person_id": person_id,
        "is_admin": bool(viewer.get("is_admin")),
        "permissions": viewer.get("permissions") or [],
    }


def _log(application, action, summary, actor, detail=None):
    row = {
        "application_id": application.get("id"),
        "candidate_id": application.get("candidate_id"),
        "activity_type": action,
        "summary": summary,
        "actor": actor["name"],
        "actor_user_id": actor["user_id"],
        "at": _now(),
    }
    if detail:
        row["detail"] = detail
    store.put("activity", row)


def _approval(job_id, action, actor, comment=None):
    store.put(
        "job_approvals",
        {
            "job_id": job_id,
            "action": action,
            "actor_name": actor["name"],
            "actor_user_id": actor["user_id"],
            "actor_id": actor["person_id"],
            "comment": comment,
            "created_at": _now(),
        },
    )


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


def _get(collection, record_id):
    """A record, or None when it is missing or not the viewer's to see."""
    if not record_id:
        return None
    try:
        return store.get(collection, record_id)
    except Exception:
        return None


def _ids(value):
    """A list of ids from a list (or a comma-separated string)."""
    if isinstance(value, str):
        value = [v for v in value.split(",")]
    out = []
    for v in value or []:
        v = str(v or "").strip()
        if v and v not in out:
            out.append(v)
    return out


def _interviewer_ids(record):
    """The panel — ``interviewer_ids`` (the import folds Orbit's join table in
    under that name), or the older ``interviewers`` list."""
    return _ids(record.get("interviewer_ids") or record.get("interviewers") or [])


def _render(text, values, escape=False):
    """Fill ``{{ merge_field }}`` from ``values``; an unknown field becomes
    empty (Orbit's rule). Values are HTML-escaped into an HTML body."""
    s = str(text or "")
    out = []
    i = 0
    while True:
        j = s.find("{{", i)
        if j < 0:
            out.append(s[i:])
            break
        k = s.find("}}", j + 2)
        if k < 0:
            out.append(s[i:])
            break
        key = s[j + 2 : k].strip()
        if key and all(c.isalnum() or c == "_" for c in key):
            value = str(values.get(key) or "")
            out.append(s[i:j])
            out.append(_esc(value) if escape else value)
            i = k + 2
        else:
            out.append(s[i : j + 2])
            i = j + 2
    return "".join(out)


def _venues():
    cal = _door("calendar")
    if cal is None:
        return []
    try:
        return cal.venues()
    except Exception:
        return []


def _when(value, venue_id=None):
    """A time the way an invite says it, in the venue's own time."""
    cal = _door("calendar")
    if cal is not None and value:
        try:
            return cal.local_time(value, venue_id)
        except Exception:
            pass
    return str(value or "").replace("T", " ")[:16]


def _link(inputs):
    mail = _door("email")
    if mail is None:
        return ""
    try:
        return mail.link(inputs)
    except Exception:
        return ""


def _hiring_manager(job, people):
    """The job's hiring manager's name — the job's own field, else its hiring
    team's manager."""
    person = people.get((job or {}).get("hiring_manager_id"))
    if person:
        return person.get("name") or ""
    if not job:
        return ""
    for row in store.list("hiring_team", where={"job_id": job["id"]}, limit=50):
        if "manager" in str(row.get("role") or "").lower():
            p = people.get(row.get("person_id"))
            if p:
                return p.get("name") or ""
    return ""


def _people():
    return _index(store.list("people", limit=5000))


def _log_email(
    candidate, application, to, subject, html, result, actor, kind, template_id=None
):
    """What the platform did with a candidate email, in the app's own email
    history and the candidate's activity — real sends only (a test goes to
    the person testing, a dry run nowhere)."""
    status = result.get("status")
    if status not in ("sent", "failed", "suppressed"):
        return
    store.put(
        "hiring_emails",
        {
            "candidate_id": candidate.get("id"),
            "application_id": (application or {}).get("id"),
            "template_id": template_id,
            "direction": "outbound",
            "kind": kind,
            "to_email": to,
            "subject": subject,
            "body_html": html,
            "status": status,
            "error": result.get("reason"),
            "email_log_id": result.get("id"),
            "sent_by_name": actor["name"],
            "sent_by_user_id": actor["user_id"],
            "created_at": _now(),
        },
    )
    if status == "sent":
        summary = "Email sent: " + subject
    elif status == "failed":
        summary = "Email failed: " + subject
    else:
        summary = "Email not sent (outside production): " + subject
    if application:
        _log(application, "email", summary, actor, {"to": to, "status": status})


def _email_outcome(results):
    """One sentence for a batch of sends."""
    counts = {}
    for r in results:
        counts[r] = counts.get(r, 0) + 1
    if counts.get("disabled"):
        return "Email is off for Norm Hiring, so nothing was sent — an org manager can turn it on in Settings."
    parts = []
    if counts.get("sent"):
        parts.append(f"{counts['sent']} sent")
    if counts.get("suppressed"):
        parts.append(
            f"{counts['suppressed']} held back (live email only goes out in production)"
        )
    if counts.get("skipped"):
        parts.append(f"{counts['skipped']} already sent")
    if counts.get("limited"):
        parts.append(f"{counts['limited']} held back (daily send limit reached)")
    if counts.get("not_allowed"):
        parts.append("no invitation sent — only someone who runs hiring can send them")
    if counts.get("failed"):
        parts.append(f"{counts['failed']} failed")
    if counts.get("dry_run"):
        parts.append(f"{counts['dry_run']} previewed, not sent")
    return ", ".join(parts)


# -- interviews: email, calendar, pack ------------------------------------------


def _remote(itype):
    return str(itype or "") in ("video", "phone", "phone_screen")


def _invite_candidate(interview, job, candidate, application, actor, params):
    """The candidate's interview invitation — a plain email, never a calendar
    guest place. Keyed on (interview, time): a second save of the same
    interview at the same time can never send it twice."""
    mail = _door("email")
    if mail is None or not interview.get("invite_candidate"):
        return None
    if not _can_email(actor):
        # The invitation goes to someone outside the org — the same gate as
        # emailing candidates. The interview itself is still saved.
        return {
            "status": "not_allowed",
            "reason": "only someone who runs hiring sends invitations",
        }
    when_at = _ts(interview.get("scheduled_at"))
    if when_at is None or when_at < datetime.datetime.now(datetime.timezone.utc):
        return None
    to = str(candidate.get("email") or "").strip()
    if not to:
        return {"status": "no_email"}
    title = (job or {}).get("title") or "the role"
    venue_id = (job or {}).get("venue_id")
    if _remote(interview.get("interview_type")):
        url = str(interview.get("meeting_url") or "").strip()
        # Only a web link becomes a link: a `javascript:` (or any other
        # scheme) a member typed must not reach a candidate as clickable.
        if url and url.lower().startswith(("https://", "http://")):
            where = f'<a href="{_esc(url)}">{_esc(url)}</a>'
        elif url:
            where = _esc(url)
        else:
            where = "We will send you a link or call you."
    else:
        where = _esc(interview.get("location") or "To be confirmed")
    first = str(candidate.get("first_name") or "").strip() or "there"
    minutes = interview.get("duration_minutes") or 30
    html = "".join(
        [
            f"<p>Hi {_esc(first)},</p>",
            f"<p>Thanks for your application for <b>{_esc(title)}</b>. We'd like to invite you to an interview.</p>",
            f"<p><b>When:</b> {_esc(_when(interview.get('scheduled_at'), venue_id))} (about {_esc(minutes)} minutes)<br><b>Where:</b> {where}</p>",
            (
                "<p>"
                + _esc(interview.get("instructions")).replace("\n", "<br>")
                + "</p>"
                if interview.get("instructions")
                else ""
            ),
            "<p>If that time doesn't suit, just reply to this email and we'll find another.</p>",
        ]
    )
    subject = "Interview invitation — " + title
    result = mail.send(
        to,
        subject,
        html,
        # One invite per booking: the round moves on with every reschedule, so
        # a move away and back (A→B→A) still sends the "back to A" invite.
        key="invite:"
        + str(interview["id"])
        + ":"
        + str(interview.get("scheduled_at"))
        + ":"
        + str(int(interview.get("schedule_round") or 0)),
        kind="interview_invite",
    )
    if params.get("_email_mode") == "live":
        _log_email(
            candidate, application, to, subject, html, result, actor, "interview_invite"
        )
    return result


def _description(rows, instructions, template_name, group):
    """The event's description: what interviewers do, the notes, and a link
    to each candidate in Norm. ``rows``: [(application, candidate, docs)]."""
    parts = [
        "<b>Interviewer instructions</b>",
        "1. Review the attached candidate pack before the interview.<br>"
        "2. Run the interview using the scorecard criteria.<br>"
        "3. Record your feedback in Norm straight after the interview, while it is fresh.",
    ]
    if template_name:
        parts.append("<b>Scorecard</b><br>" + _esc(template_name))
    if instructions:
        parts.append(
            "<b>Notes for this interview</b><br>"
            + _esc(instructions).replace("\n", "<br>")
        )
    links = []
    for application, candidate, _docs in rows:
        url = _link({"candidate": application["id"]})
        name = _esc(_candidate_name(candidate))
        links.append(f'<a href="{_esc(url)}">{name}</a>' if url else name)
    parts.append(
        (f"<b>Candidates ({len(rows)})</b><br>" if group else "<b>Candidate</b><br>")
        + "<br>".join(links)
    )
    if not group and rows:
        application, candidate, docs = rows[0]
        if docs:
            parts.append(
                f"<b>Documents ({len(docs)})</b><br>"
                + ", ".join(_esc(d.get("file_name") or "document") for d in docs)
            )
        else:
            parts.append("<b>Documents</b><br>No documents uploaded.")
        cover = str(application.get("cover_letter") or "").strip()
        if len(cover) > 6000:
            cover = cover[:6000] + "…"
        parts.append(
            "<b>Cover letter</b><br>"
            + (_esc(cover).replace("\n", "<br>") if cover else "None provided.")
        )
    return "<br><br>".join(parts)


def _docs_for(candidate_id):
    docs = store.list(
        "candidate_documents", where={"candidate_id": candidate_id}, limit=60
    )
    docs.sort(key=lambda d: str(d.get("created_at") or ""), reverse=True)
    return docs


def _sync_calendar(
    collection, record, job, rows, summary, hide_guests, default_minutes
):
    """Create or update the venue-mailbox event for ``record`` (an interview
    or a group), with a candidate pack attached. Stores the event's ids back
    on the record and returns the door's answer (``ok`` + ``message``)."""
    cal = _door("calendar")
    if cal is None:
        return {"ok": False, "message": "Calendar invites aren't available here."}
    venue_id = (job or {}).get("venue_id")
    if not record.get("google_event_id"):
        state = cal.status(venue_id)
        if not state.get("connected"):
            return {
                "ok": False,
                "message": "Saved, but no invite was sent: "
                + str(state.get("message") or ""),
            }
    if not record.get("scheduled_at"):
        return {
            "ok": False,
            "message": "Saved — add a time to send the calendar invite.",
        }
    template = _get("scorecard_templates", record.get("scorecard_template_id"))
    attach = []
    pack = None
    maker = _door("pdf")
    if maker is not None and rows:
        title = (job or {}).get("title") or "role"
        if len(rows) == 1:
            label = "Candidate pack — " + _candidate_name(rows[0][1])
        else:
            label = "Group interview pack — " + title
        pack = maker.candidate_pack(
            [
                {
                    "name": _candidate_name(candidate),
                    "job_title": (job or {}).get("title"),
                    "cover_letter": application.get("cover_letter"),
                    "documents": [
                        {
                            "file_name": d.get("file_name"),
                            "mime_type": d.get("mime_type"),
                            "file_id": d.get("file_id"),
                        }
                        for d in docs
                    ],
                }
                for application, candidate, docs in rows
            ],
            filename=label + ".pdf",
            collection=collection,
            record_id=record["id"],
            replace_file_id=record.get("pack_file_id"),
        )
        if pack.get("ok"):
            attach = [pack["file_id"]]
    result = cal.upsert(
        venue_id,
        {
            "summary": summary,
            "description": _description(
                rows,
                record.get("instructions"),
                (template or {}).get("name"),
                len(rows) != 1 or collection == "interview_groups",
            ),
            "location": record.get("location"),
            "start": record.get("scheduled_at"),
            "duration_minutes": record.get("duration_minutes") or default_minutes,
            "attendees": ["person:" + p for p in _interviewer_ids(record)],
            "hide_guests": hide_guests,
        },
        event_id=record.get("google_event_id"),
        account_id=record.get("google_account_id"),
        calendar_id=record.get("google_calendar_id"),
        attach_file_ids=attach,
        attach_title=(pack or {}).get("filename"),
        replace_drive_file_ids=record.get("pack_drive_file_ids"),
    )
    data = _writable(record)
    changed = False
    if pack and pack.get("ok"):
        data["pack_file_id"] = pack["file_id"]
        changed = True
    if result.get("ok"):
        data["google_event_id"] = result.get("event_id")
        data["google_calendar_id"] = result.get("calendar_id")
        data["google_account_id"] = result.get("account_id")
        data["pack_drive_file_ids"] = result.get("drive_file_ids") or []
        data["calendar_synced_at"] = _now()
        if not data.get("meeting_url") and result.get("hangout_link"):
            data["meeting_url"] = result.get("hangout_link")
        changed = True
    if changed:
        store.put(collection, data, record["id"])
    return result


def _unsync_calendar(collection, record, job):
    """Remove a record's event (guests are told) and forget its ids."""
    if not record.get("google_event_id"):
        return {"ok": True, "message": ""}
    cal = _door("calendar")
    if cal is None:
        return {"ok": False, "message": "Calendar invites aren't available here."}
    result = cal.delete(
        (job or {}).get("venue_id"),
        record.get("google_event_id"),
        account_id=record.get("google_account_id"),
        calendar_id=record.get("google_calendar_id"),
        drive_file_ids=record.get("pack_drive_file_ids"),
    )
    if result.get("ok"):
        data = _writable(record)
        data["google_event_id"] = None
        data["pack_drive_file_ids"] = []
        store.put(collection, data, record["id"])
    return result


def _member_rows(interviews):
    """[(application, candidate, docs)] for a list of interviews, skipping any
    whose application the viewer can't see."""
    rows = []
    for iv in interviews:
        application = _get("applications", iv.get("application_id"))
        if not application:
            continue
        candidate = (
            _get(
                "candidates", iv.get("candidate_id") or application.get("candidate_id")
            )
            or {}
        )
        rows.append(
            (
                application,
                candidate,
                _docs_for(candidate.get("id")) if candidate.get("id") else [],
            )
        )
    return rows


def _sync_single(interview, params, actor, invite):
    """Calendar + (on create/reschedule) the candidate's email for one
    interview. Returns ``{calendar, email, message}``."""
    job = _get("job_openings", interview.get("job_id"))
    rows = _member_rows([interview])
    candidate = rows[0][1] if rows else {}
    application = rows[0][0] if rows else None
    title = (job or {}).get("title") or "Role"
    cal = _sync_calendar(
        "interviews",
        interview,
        job,
        rows,
        "Interview: " + _candidate_name(candidate) + " — " + title,
        False,
        30,
    )
    mail = None
    if invite and application:
        mail = _invite_candidate(interview, job, candidate, application, actor, params)
    return {
        "calendar": cal,
        "email": mail,
        "message": _message(cal, [mail] if mail else []),
    }


def _message(cal, mails):
    parts = []
    if cal and cal.get("message"):
        parts.append(cal["message"])
    statuses = [m.get("status") for m in mails if m and m.get("status")]
    if [s for s in statuses if s == "no_email"]:
        n = len([s for s in statuses if s == "no_email"])
        parts.append(
            f"{n} candidate{'' if n == 1 else 's'} ha{'s' if n == 1 else 've'} no email address"
        )
    outcome = _email_outcome([s for s in statuses if s != "no_email"])
    if outcome:
        parts.append("Candidate invitation: " + outcome)
    return ". ".join([p.rstrip(".") for p in parts if p])


def _group_members(group_id, include_cancelled=False):
    rows = store.list("interviews", where={"group_id": group_id}, limit=500)
    if include_cancelled:
        return rows
    return [r for r in rows if r.get("status") != "cancelled"]


def _sync_group(group, params, actor, invite_ids):
    """The group's one event, plus invitations for ``invite_ids`` (members
    just added, or everyone after a reschedule)."""
    job = _get("job_openings", group.get("job_id"))
    members = _group_members(group["id"])
    if group.get("status") == "cancelled" or not members:
        cal = _unsync_calendar("interview_groups", group, job)
        return {"calendar": cal, "message": cal.get("message") or ""}
    rows = _member_rows(members)
    title = (job or {}).get("title") or "Role"
    summary = group.get("title") or (
        "Group interview: " + title + " (" + str(len(rows)) + " candidates)"
    )
    cal = _sync_calendar("interview_groups", group, job, rows, summary, True, 60)
    mails = []
    by_app = {r[0]["id"]: r for r in rows}
    for iv in members:
        if iv["id"] not in invite_ids:
            continue
        row = by_app.get(iv.get("application_id"))
        if row:
            mails.append(_invite_candidate(iv, job, row[1], row[0], actor, params))
    return {"calendar": cal, "message": _message(cal, mails)}


def _fresh(collection, record_id):
    return store.get(collection, record_id)


def _save_group(params, actor):
    """Create or edit a group interview and add members to it.

    Every member keeps its own interview row (feedback hangs off it); the
    group's details are copied onto each live member. A member added again
    after being removed gets a fresh row."""
    group_id = params.get("group_id")
    fields = params.get("fields") or {}
    current = _get("interview_groups", group_id) if group_id else None
    if group_id and not current:
        return {"error": "that group interview no longer exists"}
    # An existing group keeps its own job, whatever the page sends.
    job_id = (current or {}).get("job_id") or params.get("job_id")
    job = _get("job_openings", job_id)
    if not job:
        return {"error": "a group interview belongs to a job — choose one first"}
    if current and current.get("status") == "cancelled":
        return {"error": "that group interview was cancelled"}
    data = (
        _writable(current)
        if current
        else {
            "job_id": job_id,
            "status": "scheduled",
            "created_by": actor["person_id"],
            "created_by_user_id": actor["user_id"],
            "created_at": _now(),
        }
    )
    before = (current or {}).get("scheduled_at")
    for key in ("title",) + _GROUP_SHARED:
        if key in fields:
            data[key] = fields[key]
    data["scheduled_at"] = _iso(data.get("scheduled_at"))
    data["interview_type"] = data.get("interview_type") or "in_person"
    data["duration_minutes"] = _minutes(data.get("duration_minutes"), 60)
    data["invite_candidate"] = data.get("invite_candidate") is not False
    data["interviewer_ids"] = _ids(data.get("interviewer_ids"))
    group = store.put(
        "interview_groups",
        data,
        group_id,
        venue_id=job.get("venue_id") if not group_id else None,
    )
    rescheduled = bool(current) and _moved(before, data["scheduled_at"])

    shared = {k: data.get(k) for k in _GROUP_SHARED}
    existing = _group_members(group["id"])
    for iv in existing:
        row = _writable(iv)
        row.update(shared)
        if rescheduled:
            # A new time is a new booking (its invite key moves on), but a
            # member already interviewed or marked no-show keeps that outcome.
            row["schedule_round"] = int(row.get("schedule_round") or 0) + 1
            if row.get("status") not in ("completed", "no_show"):
                row["status"] = "scheduled"
        store.put("interviews", row, iv["id"])

    have = {iv.get("application_id") for iv in existing}
    added, skipped, invite_ids = [], [], []
    for application_id in _ids(params.get("application_ids")):
        application = _get("applications", application_id)
        if not application or application.get("job_id") != job_id:
            skipped.append(application_id)
            continue
        if application_id in have:
            skipped.append(application_id)
            continue
        row = dict(shared)
        row.update(
            {
                "application_id": application_id,
                "candidate_id": application.get("candidate_id"),
                "job_id": job_id,
                "stage_id": application.get("stage_id"),
                "group_id": group["id"],
                "status": "scheduled",
                "created_by": actor["person_id"],
                "created_by_user_id": actor["user_id"],
                "created_at": _now(),
            }
        )
        iv = store.put("interviews", row, venue_id=job.get("venue_id"))
        have.add(application_id)
        added.append(application_id)
        invite_ids.append(iv["id"])
        _log(
            application,
            "interview",
            "Added to group interview",
            actor,
            {"scheduled_at": data.get("scheduled_at"), "group_id": group["id"]},
        )
    if not current:
        invite_ids = [iv["id"] for iv in _group_members(group["id"])]
    elif rescheduled:
        invite_ids = [iv["id"] for iv in _group_members(group["id"])]
    synced = _sync_group(
        _fresh("interview_groups", group["id"]), params, actor, invite_ids
    )
    return {
        "group": _fresh("interview_groups", group["id"]),
        "added": len(added),
        "skipped": len(skipped),
        "calendar": synced.get("calendar"),
        "message": synced.get("message"),
    }


def _interview_from(params, application, actor):
    data = {
        "application_id": application["id"],
        "candidate_id": application.get("candidate_id"),
        "job_id": application.get("job_id"),
        "stage_id": application.get("stage_id"),
        "interview_type": params.get("interview_type") or "in_person",
        "scheduled_at": _iso(params.get("scheduled_at")),
        "duration_minutes": _minutes(params.get("duration_minutes"), 30),
        "location": params.get("location"),
        "status": "scheduled",
        "invite_candidate": params.get("invite_candidate") is not False,
        "interviewer_ids": _ids(
            params.get("interviewer_ids") or params.get("interviewers")
        ),
        "created_by": actor["person_id"],
        "created_by_user_id": actor["user_id"],
        "created_at": _now(),
    }
    for key in ("meeting_url", "instructions", "scorecard_template_id"):
        if params.get(key):
            data[key] = params.get(key)
    return data


# -- bulk -----------------------------------------------------------------------


def _bulk(ids, fn):
    """Run ``fn(application)`` per application — one failure never stops the
    rest. ``{done, failed, errors}``, as Orbit's "N done, M failed"."""
    done, errors = 0, []
    for application_id in _ids(ids):
        application = _get("applications", application_id)
        if not application:
            errors.append({"id": application_id, "error": "not found"})
            continue
        try:
            problem = fn(application)
        except Exception as exc:
            problem = str(exc)
        if problem:
            errors.append({"id": application_id, "error": problem})
        else:
            done += 1
    return {"done": done, "failed": len(errors), "errors": errors}


def _merge_values(application, candidate, job, manager, venue_names, interviews):
    """The merge fields Orbit offers, all filled — ``venue_name`` included
    (Orbit's is always empty because it never joins the venue)."""
    upcoming = None
    now = datetime.datetime.now(datetime.timezone.utc)
    for iv in interviews:
        at = _ts(iv.get("scheduled_at"))
        if iv.get("status") == "cancelled" or at is None or at < now:
            continue
        if upcoming is None or at < _ts(upcoming.get("scheduled_at")):
            upcoming = iv
    venue_id = (job or {}).get("venue_id")
    return {
        "candidate_first_name": candidate.get("first_name") or "",
        "candidate_last_name": candidate.get("last_name") or "",
        "job_title": (job or {}).get("title") or "",
        "venue_name": venue_names.get(venue_id) or "",
        "hiring_manager": manager,
        "interview_datetime": _when(upcoming.get("scheduled_at"), venue_id)
        if upcoming
        else "",
        "interview_location": (upcoming or {}).get("location") or "",
    }


def _recipients(application_ids):
    people = _people()
    venue_names = {v["id"]: v["name"] for v in _venues()}
    jobs = {}
    out = []
    for application_id in _ids(application_ids):
        application = _get("applications", application_id)
        if not application:
            continue
        candidate = _get("candidates", application.get("candidate_id")) or {}
        job_id = application.get("job_id")
        if job_id not in jobs:
            job = _get("job_openings", job_id)
            jobs[job_id] = (job, _hiring_manager(job, people))
        job, manager = jobs[job_id]
        interviews = store.list(
            "interviews", where={"application_id": application_id}, limit=50
        )
        out.append(
            {
                "application": application,
                "candidate": candidate,
                "values": _merge_values(
                    application, candidate, job, manager, venue_names, interviews
                ),
            }
        )
    return out


def _sync_status():
    """When the Orbit refresh last brought anything in — the newest
    imported rows' timestamps. None when nothing here came from Orbit."""
    latest = None
    for collection in ("activity", "applications", "candidates"):
        rows = store.list(
            collection,
            where={"orbit_id": {"is_null": False}},
            descending=True,
            limit=25,
        )
        for r in rows:
            value = r.get("updated_at")
            if value and (latest is None or str(value) > latest):
                latest = str(value)
    return {"refreshed_at": latest}


def _minutes(value, default):
    """A duration in minutes from whatever the page sent; a bad value is the
    default, never a crash."""
    try:
        n = int(float(value))
    except (TypeError, ValueError):
        return default
    return max(5, min(n, 24 * 60))


def _can_manage(actor):
    return actor["is_admin"] or "org:manage" in actor["permissions"]


def _can_email(actor):
    """Who may email candidates from Norm Hiring: whoever runs hiring — a
    platform admin, or a role holding hr:write, hr:hire or org:manage. Hiring
    emails go to people outside the org, so not every member."""
    held = actor["permissions"]
    return actor["is_admin"] or any(
        p in held for p in ("hr:write", "hr:hire", "org:manage")
    )


def run(params, call_api, log):
    params = params or {}
    op = params.get("op") or "board"
    actor = _actor(params)

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

    if op == "sync_status":
        return _sync_status()

    if op == "locate":
        """Where Norm asked the app to open (its component's inputs, see
        services/app_components.py): ``job`` and/or ``candidate``, as ids or
        names. Resolved HERE, where the data is, so the agent never needs
        Hiring's ids. An open job wins a name tie; a candidate's most recent
        application wins (within ``job`` when both are given)."""
        jobs = store.list("job_openings", limit=200)
        jobs.sort(key=lambda j: j.get("status") != "open")
        job = None
        if params.get("job"):
            job = _best_match(jobs, params.get("job"), lambda j: j.get("title"))
            if job is None:
                return {"error": f"No job opening matching '{params.get('job')}'"}
        application = None
        if params.get("candidate"):
            want = params.get("candidate")
            applications = store.list(
                "applications", where={"job_id": job["id"]} if job else None, limit=2000
            )
            # (the app sandbox has no next() — a loop it is)
            for a in applications:
                if str(a.get("id")) == str(want):
                    application = a
                    break
            if application is None:
                candidates = store.list("candidates", limit=2000)
                with_apps = {a.get("candidate_id") for a in applications}
                candidate = _best_match(
                    [c for c in candidates if c.get("id") in with_apps],
                    want,
                    _candidate_name,
                )
                if candidate is None:
                    where = f" for {job.get('title')}" if job else ""
                    return {"error": f"No candidate matching '{want}'{where}"}
                mine = [
                    a for a in applications if a.get("candidate_id") == candidate["id"]
                ]
                mine.sort(key=lambda a: str(a.get("created_at") or ""), reverse=True)
                application = mine[0]
        return {
            "job_id": (job or {}).get("id") or (application or {}).get("job_id"),
            "application_id": (application or {}).get("id"),
        }

    if op == "pipeline":
        job_id = params.get("job_id")
        job = store.get("job_openings", job_id)
        stages = _stages_for(job_id)
        applications = store.list("applications", where={"job_id": job_id}, limit=1000)
        candidates = _index(store.list("candidates", limit=5000))
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

    if op == "candidates":
        """Every application (or one job's), for the selectable list."""
        job_id = params.get("job_id") or None
        applications = store.list(
            "applications", where={"job_id": job_id} if job_id else None, limit=5000
        )
        candidates = _index(store.list("candidates", limit=5000))
        jobs = store.list("job_openings", limit=500)
        job_by_id = _index(jobs)
        stage_rows = (
            _stages_for(job_id) if job_id else store.list("pipeline_stages", limit=5000)
        )
        stage_by_id = _index(stage_rows)
        rows = []
        for a in applications:
            c = candidates.get(a.get("candidate_id")) or {}
            stage = stage_by_id.get(a.get("stage_id")) or {}
            rows.append(
                {
                    "id": a["id"],
                    "candidate_id": a.get("candidate_id"),
                    "job_id": a.get("job_id"),
                    "job_title": (job_by_id.get(a.get("job_id")) or {}).get("title"),
                    "stage_id": a.get("stage_id"),
                    "stage_name": stage.get("name"),
                    "status": a.get("status") or "active",
                    "rating": a.get("rating"),
                    "source": a.get("source") or c.get("source"),
                    "applied_at": a.get("applied_at") or a.get("created_at"),
                    "knockout_flag": bool(a.get("knockout_flag")),
                    "candidate_name": _candidate_name(c),
                    "candidate_email": c.get("email"),
                    "in_talent_pool": bool(c.get("in_talent_pool")),
                }
            )
        rows.sort(key=lambda r: str(r.get("applied_at") or ""), reverse=True)
        jobs.sort(key=lambda j: (j.get("status") != "open", str(j.get("title") or "")))
        return {
            "applications": rows,
            "jobs": [
                {
                    "id": j["id"],
                    "title": j.get("title"),
                    "status": j.get("status"),
                    "venue_id": j.get("venue_id"),
                }
                for j in jobs
            ],
            "stages": stage_rows if job_id else [],
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
        people = _people()
        for iv in interviews:
            iv["interviewer_ids"] = _interviewer_ids(iv)
            iv["interviewer_names"] = [
                (people.get(p) or {}).get("name") or "Unknown"
                for p in iv["interviewer_ids"]
            ]
            if iv.get("group_id"):
                g = _get("interview_groups", iv["group_id"]) or {}
                iv["group_title"] = g.get("title") or "Group interview"
        interviews.sort(key=lambda r: str(r.get("scheduled_at") or ""))
        emails = store.list(
            "hiring_emails", where={"candidate_id": candidate["id"]}, limit=100
        )
        emails.sort(key=lambda r: str(r.get("created_at") or ""), reverse=True)
        activity.sort(key=lambda r: str(r.get("at") or ""), reverse=True)
        notes.sort(key=lambda r: str(r.get("at") or ""), reverse=True)
        job = _get("job_openings", application.get("job_id")) or {}
        return {
            "application": application,
            "candidate": {**candidate, "name": _candidate_name(candidate)},
            "job": {"id": job.get("id"), "title": job.get("title")},
            "answers": sorted(answers, key=lambda r: r.get("sort_index") or 0),
            "notes": notes,
            "activity": activity,
            "interviews": interviews,
            "emails": [
                {
                    k: e.get(k)
                    for k in (
                        "id",
                        "subject",
                        "status",
                        "to_email",
                        "created_at",
                        "sent_by_name",
                        "kind",
                    )
                }
                for e in emails
            ],
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

    if op == "bulk_move":
        stage = store.get("pipeline_stages", params.get("stage_id"))
        touched = set()

        def move_one(application):
            if stage.get("job_id") != application.get("job_id"):
                return "that stage belongs to a different job"
            store.put(
                "applications", _apply_stage(application, stage), application["id"]
            )
            touched.add(application.get("job_id"))
            _log(
                application,
                "stage_change",
                "Moved to " + str(stage.get("name") or "a new stage"),
                actor,
            )
            return None

        out = _bulk(params.get("application_ids"), move_one)
        for job_id in touched:
            _recount_fill(job_id)
        return out

    if op == "bulk_talent_pool":
        on = params.get("in_talent_pool") is not False

        def pool_one(application):
            candidate = store.get("candidates", application.get("candidate_id"))
            data = _writable(candidate)
            data["in_talent_pool"] = on
            store.put("candidates", data, candidate["id"])
            _log(
                application,
                "talent_pool",
                "Added to talent pool" if on else "Removed from talent pool",
                actor,
            )
            return None

        return _bulk(params.get("application_ids"), pool_one)

    if op in ("reject", "bulk_reject"):
        """Not progressing: to the job's rejected stage, the reason recorded.
        The bulk form demands a reason (Orbit's rule) — a whole batch turned
        away with no why is the record nobody can explain later."""
        reason = str(params.get("reason") or "").strip()
        if op == "bulk_reject" and not reason:
            return {
                "error": "say why they're not progressing — the reason is recorded on each candidate"
            }
        touched = set()

        def reject_one(application):
            rejected = None
            for s in _stages_for(application.get("job_id")):
                if s.get("stage_type") == "rejected":
                    rejected = s
                    break
            if not rejected:
                return "this job has no 'rejected' stage"
            updated = _apply_stage(application, rejected)
            updated["rejection_reason"] = reason or None
            store.put("applications", updated, application["id"])
            touched.add(application.get("job_id"))
            _log(
                application,
                "rejected",
                "Not progressing" + (": " + reason if reason else ""),
                actor,
            )
            return None

        if op == "reject":
            application = store.get("applications", params.get("application_id"))
            problem = reject_one(application)
            if problem:
                return {"error": problem}
            _recount_fill(application.get("job_id"))
            return {"ok": True}
        out = _bulk(params.get("application_ids"), reject_one)
        for job_id in touched:
            _recount_fill(job_id)
        return out

    if op == "email_compose":
        """Who a bulk email would reach, with each one's merge values, and
        the templates — the page fills the preview from these."""
        recipients = _recipients(params.get("application_ids"))
        templates = [
            {k: t.get(k) for k in ("id", "name", "subject", "body_html")}
            for t in store.list("email_templates", limit=200)
            if t.get("is_active") is not False
        ]
        templates.sort(key=lambda t: str(t.get("name") or ""))
        mail = _door("email")
        return {
            "templates": templates,
            "recipients": [
                {
                    "application_id": r["application"]["id"],
                    "name": _candidate_name(r["candidate"]),
                    "email": r["candidate"].get("email"),
                    "values": r["values"],
                }
                for r in recipients
            ],
            "enabled": bool(mail and mail.enabled()),
        }

    if op == "bulk_email":
        """One personalised email per candidate. Keyed on the batch the page
        names, so a double click or a retry of the same send never reaches
        anyone twice."""
        if not _can_email(actor):
            return {"error": "Only someone who runs hiring can email candidates."}
        subject = str(params.get("subject") or "").strip()
        body = str(params.get("body_html") or "").strip()
        if not subject or not body:
            return {"error": "an email needs a subject and a message"}
        mail = _door("email")
        if mail is None:
            return {"error": "email isn't available here"}
        mode = params.get("_email_mode")
        batch = str(params.get("batch_id") or _now())
        statuses, no_email, previews = [], 0, []
        for r in _recipients(params.get("application_ids")):
            candidate, application = r["candidate"], r["application"]
            to = str(candidate.get("email") or "").strip()
            if not to:
                no_email += 1
                continue
            s = _render(subject, r["values"])
            h = _render(body, r["values"], escape=True)
            result = mail.send(
                to,
                s,
                h,
                key="bulk:" + batch + ":" + application["id"],
                kind="candidate_email",
                # The body is what a person typed in the composer: keep its
                # simple formatting, drop anything else (scripts, styles,
                # tracking pixels, javascript: links).
                sanitize=True,
            )
            statuses.append(result.get("status"))
            if mode == "dry_run":
                previews.append({"to": to, "subject": s, "html": result.get("html")})
            elif mode == "live":
                _log_email(
                    candidate,
                    application,
                    to,
                    s,
                    h,
                    result,
                    actor,
                    "candidate_email",
                    params.get("template_id"),
                )
            if mode == "test" or result.get("status") == "disabled":
                break  # a test is one copy, to the tester; off is off for all
        message = _email_outcome(statuses)
        if mode == "test" and statuses:
            message = "A test copy went to you" if statuses[0] == "sent" else message
        if no_email:
            message = (message + ". " if message else "") + (
                f"{no_email} had no email address and were skipped"
            )
        return {
            "statuses": statuses,
            "no_email": no_email,
            "previews": previews,
            "message": message,
        }

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
                "author": actor["name"],
                "author_user_id": actor["user_id"],
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

        Only someone who runs hiring may hire: a person record decides who
        counts as one of the org's own for email and calendar invitations.
        """
        if not _can_email(actor):
            return {"error": "Only someone who runs hiring can hire."}
        if (params.get("role") or "team_member") not in (
            "team_member",
            "manager",
            "head_office",
        ):
            return {"error": "Role must be team member, manager or head office."}
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
            email_addr = candidate.get("email")
            existing = (
                store.list("people", where={"email": email_addr}, limit=5)
                if email_addr
                else []
            )
            if existing:
                person_id = existing[0]["id"]
            else:
                person = store.put(
                    "people",
                    {
                        "name": _candidate_name(candidate),
                        "email": email_addr,
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
            c for c in store.list("candidates", limit=5000) if c.get("in_talent_pool")
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
                "created_by": actor["person_id"],
                "created_by_user_id": actor["user_id"],
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

    if op == "duplicate_job":
        """A copy of a job — its details, pipeline, application form and
        hiring team — as a draft called "<title> (Copy)", with a record of
        where it came from (Orbit's duplicate)."""
        source = store.get("job_openings", params.get("job_id"))
        data = {
            k: v
            for k, v in _writable(source).items()
            if k not in _NOT_COPIED and not str(k).endswith("_orbit_id")
        }
        title = str(source.get("title") or "Untitled role") + " (Copy)"
        data.update(
            {
                "title": title,
                "status": "draft",
                "is_published": False,
                "positions_filled": 0,
                "created_at": _now(),
                "created_by": actor["person_id"],
                "created_by_user_id": actor["user_id"],
            }
        )
        job = store.put("job_openings", data, venue_id=source.get("venue_id"))
        for s in _stages_for(source["id"]):
            row = {
                k: v
                for k, v in _writable(s).items()
                if k != "orbit_id" and not str(k).endswith("_orbit_id")
            }
            row["job_id"] = job["id"]
            store.put("pipeline_stages", row)
        for f in store.list(
            "application_fields", where={"job_id": source["id"]}, limit=200
        ):
            row = {
                k: v
                for k, v in _writable(f).items()
                if k != "orbit_id" and not str(k).endswith("_orbit_id")
            }
            row["job_id"] = job["id"]
            store.put("application_fields", row)
        for t in store.list("hiring_team", where={"job_id": source["id"]}, limit=100):
            row = {
                k: v
                for k, v in _writable(t).items()
                if k != "orbit_id" and not str(k).endswith("_orbit_id")
            }
            row["job_id"] = job["id"]
            store.put("hiring_team", row)
        _approval(
            job["id"],
            "duplicated",
            actor,
            'Duplicated from "' + str(source.get("title") or "") + '"',
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
        """Publish / unpublish / close — status and is_published are
        orthogonal, so both are settable. Publishing a job that isn't open
        yet (a draft, or one just reopened) opens it, as Orbit does."""
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
            if (
                params["is_published"]
                and "status" not in params
                and data.get("status")
                in (
                    None,
                    "draft",
                    "approved",
                    "on_hold",
                    "pending_approval",
                )
            ):
                data["status"] = "open"
        job = store.put("job_openings", data, job_id)
        if "status" in params and params["status"] == "closed":
            _approval(job_id, "closed", actor)
        elif "is_published" in params:
            _approval(
                job_id, "published" if params["is_published"] else "unpublished", actor
            )
        return {"job": job}

    if op == "reopen_job":
        """A closed job back in play — approved but not yet published, so it
        doesn't reappear on the careers page by surprise (Orbit's reopen)."""
        job_id = params.get("job_id")
        current = store.get("job_openings", job_id)
        if current.get("status") != "closed":
            return {"error": "only a closed job can be reopened"}
        data = _writable(current)
        data.update({"status": "approved", "is_published": False, "closed_at": None})
        job = store.put("job_openings", data, job_id)
        _approval(job_id, "reopened", actor)
        return {"job": job}

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
        email_addr = str(params.get("email") or "").strip() or None
        if not (first or last or email_addr):
            return {"error": "a name or an email is required"}
        # Reuse an existing candidate by email rather than duplicating.
        candidate_id = None
        if email_addr:
            match = store.list("candidates", where={"email": email_addr}, limit=1)
            if match:
                candidate_id = match[0]["id"]
        if not candidate_id:
            candidate_id = store.put(
                "candidates",
                {
                    "first_name": first,
                    "last_name": last,
                    "email": email_addr,
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

    if op == "interview_form":
        """What the interview dialogs choose from: the team (interviewers),
        scorecard templates, and — for a job — its upcoming group
        interviews ("date · N candidates")."""
        people = [
            {
                "id": p["id"],
                "name": p.get("name") or p.get("email") or "Unnamed",
                "email": p.get("email"),
                "role": p.get("role"),
            }
            for p in store.list("people", limit=5000)
            if p.get("is_active") is not False
        ]
        people.sort(key=lambda p: str(p.get("name") or "").lower())
        templates = [
            {"id": t["id"], "name": t.get("name")}
            for t in store.list("scorecard_templates", limit=200)
            if t.get("is_active") is not False
        ]
        groups = []
        job_id = params.get("job_id")
        if job_id:
            soon = datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(
                hours=1
            )
            for g in store.list(
                "interview_groups", where={"job_id": job_id}, limit=200
            ):
                at = _ts(g.get("scheduled_at"))
                if g.get("status") == "scheduled" and at is not None and at >= soon:
                    groups.append({**g, "member_count": len(_group_members(g["id"]))})
            groups.sort(key=lambda g: str(g.get("scheduled_at") or ""))
        out = {"people": people, "templates": templates, "groups": groups}
        if params.get("group_id"):
            group = store.get("interview_groups", params["group_id"])
            group["interviewer_ids"] = _interviewer_ids(group)
            members = _member_rows(_group_members(group["id"]))
            out["group"] = group
            out["members"] = [
                {"application_id": a["id"], "name": _candidate_name(c)}
                for a, c, _d in members
            ]
        if params.get("interview_id"):
            iv = store.get("interviews", params["interview_id"])
            iv["interviewer_ids"] = _interviewer_ids(iv)
            out["interview"] = iv
        return out

    if op == "schedule_interview":
        """Record an interview, send its invite from the venue mailbox, and
        invite the candidate by email (once). `scheduled_at` carries a time,
        not just a date; the panel, meeting link and briefing ride along."""
        application = store.get("applications", params.get("application_id"))
        job = _get("job_openings", application.get("job_id")) or {}
        data = _interview_from(params, application, actor)
        interview = store.put("interviews", data, venue_id=job.get("venue_id"))
        _log(
            application,
            "interview_scheduled",
            "Interview scheduled"
            + (
                " for " + _when(data["scheduled_at"], job.get("venue_id"))
                if data.get("scheduled_at")
                else ""
            ),
            actor,
        )
        synced = _sync_single(interview, params, actor, True)
        return {
            "interview": _fresh("interviews", interview["id"]),
            "calendar": synced["calendar"],
            "email": synced["email"],
            "message": synced["message"],
        }

    if op == "update_interview":
        interview = store.get("interviews", params.get("interview_id"))
        if interview.get("group_id"):
            return {
                "error": "this interview is part of a group interview — change it from the Interviews tab"
            }
        if interview.get("status") == "cancelled":
            return {"error": "that interview was cancelled — schedule a new one"}
        fields = params.get("fields") or {}
        data = _writable(interview)
        before = _iso(interview.get("scheduled_at"))
        for key in _GROUP_SHARED:
            if key in fields:
                data[key] = fields[key]
        data["scheduled_at"] = _iso(data.get("scheduled_at"))
        data["interviewer_ids"] = _ids(data.get("interviewer_ids"))
        data.pop("interviewers", None)
        rescheduled = _moved(before, data["scheduled_at"])
        if rescheduled:
            data["status"] = "scheduled"
            data["schedule_round"] = int(data.get("schedule_round") or 0) + 1
        interview = store.put("interviews", data, interview["id"])
        application = _get("applications", interview.get("application_id"))
        if application:
            job = _get("job_openings", interview.get("job_id")) or {}
            _log(
                application,
                "interview_updated",
                (
                    "Interview moved to "
                    + _when(data["scheduled_at"], job.get("venue_id"))
                )
                if rescheduled and data.get("scheduled_at")
                else "Interview details updated",
                actor,
            )
        synced = _sync_single(interview, params, actor, rescheduled)
        return {
            "interview": _fresh("interviews", interview["id"]),
            "calendar": synced["calendar"],
            "email": synced["email"],
            "message": synced["message"],
        }

    if op in ("cancel_interview", "remove_group_member"):
        """Cancel one interview. A group member is taken off the group (their
        row is kept, cancelled) and the group's invite updated."""
        interview = store.get("interviews", params.get("interview_id"))
        if interview.get("status") == "cancelled":
            return {"ok": True, "message": "Already cancelled"}
        data = _writable(interview)
        data["status"] = "cancelled"
        store.put("interviews", data, interview["id"])
        application = _get("applications", interview.get("application_id"))
        group = _get("interview_groups", interview.get("group_id"))
        if application:
            _log(
                application,
                "interview_cancelled",
                "Removed from group interview" if group else "Interview cancelled",
                actor,
            )
        if group:
            synced = _sync_group(group, params, actor, [])
            return {
                "ok": True,
                "message": synced.get("message") or "Removed — invite updated",
            }
        cal = _unsync_calendar(
            "interviews",
            _fresh("interviews", interview["id"]),
            _get("job_openings", interview.get("job_id")),
        )
        return {"ok": True, "message": cal.get("message") or "Interview cancelled"}

    if op == "save_group":
        return _save_group(params, actor)

    if op == "cancel_group":
        group = store.get("interview_groups", params.get("group_id"))
        data = _writable(group)
        data["status"] = "cancelled"
        store.put("interview_groups", data, group["id"])
        for iv in _group_members(group["id"]):
            row = _writable(iv)
            row["status"] = "cancelled"
            store.put("interviews", row, iv["id"])
            application = _get("applications", iv.get("application_id"))
            if application:
                _log(
                    application,
                    "interview_cancelled",
                    "Group interview cancelled",
                    actor,
                )
        cal = _unsync_calendar(
            "interview_groups",
            _fresh("interview_groups", group["id"]),
            _get("job_openings", group.get("job_id")),
        )
        return {
            "ok": True,
            "message": cal.get("message") or "Group interview cancelled",
        }

    if op == "interviews":
        """Every interview in a window — upcoming, past (most recent first)
        or a date range — with a group shown as one row of its members.
        The page filters by job, venue, interviewer and status."""
        when = params.get("when") or "upcoming"
        now = datetime.datetime.now(datetime.timezone.utc)
        lo = (
            _ts(params.get("from"))
            if when == "range"
            else (now if when == "upcoming" else None)
        )
        hi = (
            _ts(params.get("to"))
            if when == "range"
            else (now if when == "past" else None)
        )
        jobs = _index(store.list("job_openings", limit=500))
        groups = _index(store.list("interview_groups", limit=2000))
        candidates = _index(store.list("candidates", limit=5000))
        people = _people()
        venues = _venues()
        venue_names = {v["id"]: v["name"] for v in venues}
        rows = {}
        for iv in store.list("interviews", limit=5000):
            at = _ts(iv.get("scheduled_at"))
            if (
                at is None
                or (lo is not None and at < lo)
                or (hi is not None and at > hi)
            ):
                continue
            group = groups.get(iv.get("group_id")) if iv.get("group_id") else None
            key = (group or {}).get("id") or iv["id"]
            row = rows.get(key)
            if row is None:
                source = group or iv
                job = jobs.get(source.get("job_id") or iv.get("job_id")) or {}
                ids = _interviewer_ids(source)
                row = {
                    "key": key,
                    "group_id": (group or {}).get("id"),
                    "title": (group or {}).get("title"),
                    "scheduled_at": source.get("scheduled_at"),
                    "duration_minutes": source.get("duration_minutes"),
                    "interview_type": source.get("interview_type"),
                    "location": source.get("location"),
                    "status": source.get("status") or "scheduled",
                    "job": {
                        "id": job.get("id"),
                        "title": job.get("title"),
                        "venue_id": job.get("venue_id"),
                    },
                    "venue_name": venue_names.get(job.get("venue_id")),
                    "interviewer_ids": ids,
                    "interviewer_names": [
                        (people.get(p) or {}).get("name") or "Unknown" for p in ids
                    ],
                    "calendar": bool(source.get("google_event_id")),
                    "members": [],
                }
                rows[key] = row
            if group and iv.get("status") == "cancelled":
                continue
            row["members"].append(
                {
                    "interview_id": iv["id"],
                    "application_id": iv.get("application_id"),
                    "candidate_name": _candidate_name(
                        candidates.get(iv.get("candidate_id")) or {}
                    ),
                    "status": iv.get("status") or "scheduled",
                }
            )
        out = sorted(
            rows.values(),
            key=lambda r: (_ts(r.get("scheduled_at")) or now).timestamp(),
            reverse=when == "past",
        )
        return {
            "rows": out,
            "jobs": sorted(
                [{"id": j["id"], "title": j.get("title")} for j in jobs.values()],
                key=lambda j: str(j.get("title") or ""),
            ),
            "venues": [
                {"id": v["id"], "name": v["name"], "mailbox": v.get("mailbox")}
                for v in venues
            ],
            "people": sorted(
                [
                    {"id": p["id"], "name": p.get("name") or "Unnamed"}
                    for p in people.values()
                    if p.get("is_active") is not False
                ],
                key=lambda p: str(p.get("name") or "").lower(),
            ),
        }

    if op == "settings":
        """Email (on/off, the log) and which venues have a mailbox for
        calendar invites."""
        mail = _door("email")
        return {
            "email_enabled": bool(mail and mail.enabled()),
            "can_manage": _can_manage(actor),
            "log": mail.log(100) if mail else [],
            "venues": _venues(),
        }

    if op == "set_email_enabled":
        mail = _door("email")
        if mail is None:
            return {"error": "email isn't available here"}
        result = mail.set_enabled(bool(params.get("enabled")))
        if not result.get("ok"):
            return {"error": result.get("reason") or "that wasn't allowed"}
        return result

    if op == "email_preview":
        mail = _door("email")
        html = mail.preview(params.get("log_id")) if mail else None
        if html is None:
            return {"error": "that email isn't available"}
        return {"html": html}

    return {"error": "unknown op '" + str(op) + "'"}
