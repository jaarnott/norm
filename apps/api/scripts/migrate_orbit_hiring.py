"""Mirror Orbit's hiring data into the Norm Hiring app's storage.

Same contract as the training mirror (see migrate_orbit_training.py): read-only
against Orbit, keyed by ``data->>'orbit_id'``, unchanged rows left alone, rows
Orbit no longer has deleted (Norm-created rows never touched), and writing into
the ``hr_suite`` namespace the two apps share — so a candidate hired here
becomes a person the Training app can enrol without anything being copied
between them.

Run AFTER the training mirror: person references (hiring managers,
interviewers, the hired candidate's person record) resolve against the people
it wrote. A reference to someone not carried keeps its raw Orbit id beside it.

The pipeline has one rule worth carrying carefully: an application's
``status`` is DERIVED from its stage's ``stage_type``, never from the stage's
name, which users rename freely.

    uv run python scripts/migrate_orbit_hiring.py --org-email admin@norm.local --dry-run
    uv run python scripts/migrate_orbit_hiring.py --org-email admin@norm.local
"""

from __future__ import annotations

import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from scripts.migrate_orbit_training import (  # noqa: E402 — path set above
    Mirror,
    Orbit,
    RunResult,
    TargetError,
    _pick,
    build_parser,
    copied_files,
    finish,
    person_ref,
    person_refs,
    resolve_org,
    storage_ref,
    venue_map_for,
)

#: The collections THIS script owns — the only ones its delete pass may touch.
#: (``people`` belongs to the training mirror.)
HIRING_COLLECTIONS = (
    "scorecard_templates",
    "email_templates",
    "job_openings",
    "pipeline_stages",
    "application_fields",
    "candidates",
    "applications",
    "answers",
    "notes",
    "activity",
    "interview_groups",
    "interviews",
    "interview_scorecards",
    "job_approvals",
    "hiring_team",
    "jd_templates",
    "careers_settings",
    "candidate_documents",
    "hiring_emails",
)

#: Orbit's private bucket for CVs and other candidate uploads.
CV_BUCKET = "candidate-documents"


def run(
    db,
    orbit,
    *,
    org_email: str,
    org_id: str | None = None,
    expect_org: str | None = None,
    dry_run: bool = False,
    allow_mass_delete: bool = False,
    log=print,
) -> RunResult:
    try:
        user, org = resolve_org(db, org_email, org_id, expect_org)
    except TargetError as exc:
        log(str(exc))
        return RunResult(code=1)
    log(f"target org: {org.name} ({org.id}) — via {user.email}")
    org_id = org.id

    venue_map = venue_map_for(db, orbit, org_id, log)
    mirror = Mirror(
        db, org_id=org_id, user_id=user.id, dry_run=dry_run, owned=HIRING_COLLECTIONS
    )
    copied = copied_files(db, org_id)
    #: People the training mirror wrote, by Orbit team-member id.
    people = mirror.existing_ids("people")
    if not people:
        log(
            "  ! no mirrored people in this org yet — run the training mirror "
            "first, or every person reference stays a raw Orbit id"
        )
    ids = mirror.ids
    #: Rows deliberately not migrated, and why. A migration that drops
    #: something MUST say so — a silent skip reads as "there was nothing
    #: there", which is the one thing a reconciliation must never let you
    #: believe.
    skipped: list[str] = []
    notes: dict[str, str] = {}

    log("reading Orbit…")

    # Templates first: pipeline stages reference a scorecard template and an
    # email template by id, so those ids must exist before stages migrate.
    crit_by_template: dict[str, list[dict]] = {}
    for cr in orbit.rows("scorecard_criteria"):
        # The criterion keeps its Orbit id: a scorecard rating names the
        # criterion it scores, and without the id that link is lost (the
        # August import dropped it).
        crit_by_template.setdefault(cr["template_id"], []).append(
            {"id": cr["id"], **_pick(cr, "name", "description", "sort_index")}
        )
    templates = orbit.rows("scorecard_templates")
    mirror.source("scorecard_templates", [t["id"] for t in templates])
    for t in templates:
        data = _pick(t, "name", "description")
        data["is_active"] = bool(t.get("is_active"))
        data["criteria"] = sorted(
            crit_by_template.get(t["id"], []),
            key=lambda c: (c.get("sort_index") or 0, c["id"]),
        )
        mirror.write("scorecard_templates", t["id"], data)

    email_templates = orbit.rows("hiring_email_templates")
    mirror.source("email_templates", [t["id"] for t in email_templates])
    for t in email_templates:
        data = _pick(t, "name", "template_key", "subject", "body_html")
        data["is_active"] = bool(t.get("is_active"))
        mirror.write("email_templates", t["id"], data)

    jobs = orbit.rows("job_openings")
    mirror.source("job_openings", [j["id"] for j in jobs])
    for j in jobs:
        data = _pick(
            j,
            "title",
            "slug",
            "department",
            "employment_type",
            "hours_per_week",
            "status",
            "positions_to_fill",
            "positions_filled",
            "pay_type",
            "pay_min",
            "pay_max",
            "pay_currency",
            "target_start_date",
            "description_html",
            "requirements_html",
            "benefits_html",
            "about_html",
            "published_at",
            "closed_at",
            "auto_email_on_stage",
            "created_at",
        )
        data["is_published"] = bool(j.get("is_published"))
        data["pay_public"] = bool(j.get("pay_public"))
        person_ref(data, "hiring_manager_id", j.get("hiring_manager_id"), people)
        person_ref(data, "owner_id", j.get("owner_id"), people)
        person_ref(data, "created_by", j.get("created_by"), people)
        mirror.write("job_openings", j["id"], data, venue_map.get(j.get("venue_id")))

    stage_types = {}
    stages = orbit.rows("job_pipeline_stages")
    mirror.source("pipeline_stages", [s["id"] for s in stages])
    for s in stages:
        job = ids["job_openings"].get(s["job_id"])
        if not job:
            skipped.append(f"stage {s['id'][:8]}… — job not migrated")
            continue
        data = _pick(s, "name", "stage_type", "sort_index")
        data["job_id"] = job
        data.setdefault("stage_type", "active")
        # Per-stage automation: which scorecard/email template fires here.
        if s.get("scorecard_template_id"):
            data["scorecard_template_id"] = ids["scorecard_templates"].get(
                s["scorecard_template_id"]
            )
        if s.get("email_template_id"):
            data["email_template_id"] = ids["email_templates"].get(
                s["email_template_id"]
            )
        stage_types[s["id"]] = data["stage_type"]
        mirror.write("pipeline_stages", s["id"], data)

    fields = orbit.rows("job_application_fields")
    mirror.source("application_fields", [f["id"] for f in fields])
    for f in fields:
        job = ids["job_openings"].get(f["job_id"])
        if not job:
            skipped.append(f"field {f['id'][:8]}… — job not migrated")
            continue
        data = _pick(
            f,
            "field_key",
            "label",
            "field_type",
            "help_text",
            "options",
            "knockout_values",
            "sort_index",
        )
        data["job_id"] = job
        for flag in ("is_required", "is_enabled", "is_standard", "knockout_enabled"):
            data[flag] = bool(f.get(flag))
        mirror.write("application_fields", f["id"], data)

    candidates = orbit.rows("candidates")
    mirror.source("candidates", [c["id"] for c in candidates])
    for c in candidates:
        data = _pick(
            c,
            "first_name",
            "last_name",
            "email",
            "phone",
            "location",
            "source",
            "source_detail",
            "tags",
            "talent_pool_note",
            "linkedin_url",
            "created_at",
        )
        data["in_talent_pool"] = bool(c.get("in_talent_pool"))
        mirror.write("candidates", c["id"], data)

    applications = orbit.rows("candidate_applications")
    mirror.source("applications", [a["id"] for a in applications])
    for a in applications:
        job = ids["job_openings"].get(a["job_id"])
        candidate = ids["candidates"].get(a["candidate_id"])
        if not job or not candidate:
            why = "no job_id in Orbit" if not a.get("job_id") else "job not migrated"
            if not candidate:
                why = "candidate not migrated"
            skipped.append(f"application {a['id'][:8]}… — {why}")
            continue
        data = _pick(
            a,
            "status",
            "rating",
            "source",
            "applied_at",
            "stage_changed_at",
            "rejected_at",
            "rejection_reason",
            "hired_at",
            "hire_start_date",
            "availability",
            "cover_letter",
        )
        data["job_id"] = job
        data["candidate_id"] = candidate
        data["stage_id"] = ids["pipeline_stages"].get(a.get("stage_id"))
        data["knockout_flag"] = bool(a.get("knockout_flag"))
        # Status is derived from the stage's TYPE, never its name. Orbit
        # stores both and they can drift; the stage is the authority.
        kind = stage_types.get(a.get("stage_id"))
        if kind == "hired":
            data["status"] = "hired"
        elif kind == "rejected":
            data["status"] = "rejected"
        elif kind:
            data["status"] = "active"
        # A hired candidate is already a person if training knows them.
        person_ref(data, "person_id", a.get("team_member_id"), people)
        mirror.write("applications", a["id"], data)

    answers = orbit.rows("candidate_application_answers")
    mirror.source("answers", [r["id"] for r in answers])
    for r in answers:
        application = ids["applications"].get(r["application_id"])
        if not application:
            skipped.append(f"answer {r['id'][:8]}… — application not migrated")
            continue
        data = _pick(r, "field_key", "label", "value")
        data["application_id"] = application
        if ids["application_fields"].get(r.get("field_id")):
            data["field_id"] = ids["application_fields"][r["field_id"]]
        mirror.write("answers", r["id"], data)

    # Notes/activity can hang off a candidate WITHOUT an application (Orbit
    # allows candidate-level notes). Carry both keys, keep whichever resolves.
    notes_rows = orbit.rows("candidate_notes")
    mirror.source("notes", [n["id"] for n in notes_rows])
    for n in notes_rows:
        candidate = ids["candidates"].get(n.get("candidate_id"))
        application = ids["applications"].get(n.get("application_id"))
        if not candidate and not application:
            skipped.append(
                f"note {n['id'][:8]}… — neither candidate nor application migrated"
            )
            continue
        data = _pick(n, "body", "mentions", "author_name", "created_at")
        if application:
            data["application_id"] = application
        if candidate:
            data["candidate_id"] = candidate
        data["author"] = n.get("author_name")
        data["at"] = n.get("created_at")
        mirror.write("notes", n["id"], data)

    activity = orbit.rows("candidate_activity")
    mirror.source("activity", [a["id"] for a in activity])
    for a in activity:
        candidate = ids["candidates"].get(a.get("candidate_id"))
        application = ids["applications"].get(a.get("application_id"))
        if not candidate and not application:
            skipped.append(
                f"activity {a['id'][:8]}… — neither candidate nor application migrated"
            )
            continue
        data = _pick(a, "activity_type", "summary", "detail", "actor_name")
        if application:
            data["application_id"] = application
        if candidate:
            data["candidate_id"] = candidate
        data["actor"] = a.get("actor_name") or "Orbit"
        data["at"] = a.get("created_at")
        mirror.write("activity", a["id"], data)

    # ---- interviews: groups first, then interviews (which may belong to one)
    groups = orbit.rows("interview_groups")
    mirror.source("interview_groups", [g["id"] for g in groups])
    for g in groups:
        data = _pick(
            g,
            "title",
            "interview_type",
            "scheduled_at",
            "duration_minutes",
            "location",
            "meeting_url",
            "instructions",
            "invite_candidate",
            "status",
            "google_event_id",
            "google_calendar_id",
            "google_account_id",
            "created_at",
            "updated_at",
        )
        if g.get("job_id"):
            data["job_orbit_id"] = g["job_id"]
            if ids["job_openings"].get(g["job_id"]):
                data["job_id"] = ids["job_openings"][g["job_id"]]
        if g.get("scorecard_template_id"):
            data["scorecard_template_id"] = ids["scorecard_templates"].get(
                g["scorecard_template_id"]
            )
        person_refs(data, "interviewer_ids", g.get("interviewer_ids"), people)
        person_ref(data, "created_by", g.get("created_by"), people)
        mirror.write("interview_groups", g["id"], data)

    # Interviewers are a join table in Orbit; folded into the interview here
    # (an interview is read whole and its panel has no life of its own).
    panel: dict[str, list[str]] = {}
    for ii in orbit.rows("interview_interviewers"):
        if ii.get("team_member_id"):
            panel.setdefault(ii["interview_id"], []).append(ii["team_member_id"])

    interviews = orbit.rows("interviews")
    mirror.source("interviews", [i["id"] for i in interviews])
    for i in interviews:
        application = ids["applications"].get(i.get("application_id"))
        if not application:
            skipped.append(f"interview {i['id'][:8]}… — application not migrated")
            continue
        data = _pick(
            i,
            "interview_type",
            "scheduled_at",
            "duration_minutes",
            "location",
            "meeting_url",
            "instructions",
            "status",
            "invite_candidate",
            "google_event_id",
            "google_calendar_id",
            "google_account_id",
            "created_at",
            "updated_at",
        )
        data["application_id"] = application
        for key, collection in (
            ("candidate_id", "candidates"),
            ("job_id", "job_openings"),
            ("stage_id", "pipeline_stages"),
            ("scorecard_template_id", "scorecard_templates"),
        ):
            if ids[collection].get(i.get(key)):
                data[key] = ids[collection][i[key]]
        if i.get("group_id"):
            data["group_orbit_id"] = i["group_id"]
            if ids["interview_groups"].get(i["group_id"]):
                data["group_id"] = ids["interview_groups"][i["group_id"]]
        person_refs(data, "interviewer_ids", panel.get(i["id"]), people)
        person_ref(data, "created_by", i.get("created_by"), people)
        mirror.write("interviews", i["id"], data)

    # Scorecards carry their ratings nested (a scorecard is read whole); each
    # rating names its criterion by the Orbit id kept on the template above.
    ratings_by_card: dict[str, list[dict]] = {}
    for r in orbit.rows("scorecard_ratings"):
        ratings_by_card.setdefault(r["scorecard_id"], []).append(
            {
                "id": r["id"],
                **_pick(r, "criterion_id", "criterion_name", "rating", "comment"),
            }
        )
    cards = orbit.rows("interview_scorecards")
    mirror.source("interview_scorecards", [c["id"] for c in cards])
    for c in cards:
        interview = ids["interviews"].get(c.get("interview_id"))
        application = ids["applications"].get(c.get("application_id"))
        if not interview and not application:
            skipped.append(
                f"scorecard {c['id'][:8]}… — neither interview nor application migrated"
            )
            continue
        data = _pick(
            c,
            "interviewer_name",
            "overall_recommendation",
            "comments",
            "submitted_at",
            "created_at",
            "updated_at",
        )
        if interview:
            data["interview_id"] = interview
        if application:
            data["application_id"] = application
        if c.get("template_id"):
            data["template_id"] = ids["scorecard_templates"].get(c["template_id"])
        person_ref(data, "interviewer_id", c.get("interviewer_id"), people)
        data["ratings"] = ratings_by_card.get(c["id"], [])
        mirror.write("interview_scorecards", c["id"], data)

    # ---- job-level: approvals, hiring team, JD templates, careers ------
    approvals = orbit.rows("job_opening_approvals")
    mirror.source("job_approvals", [ap["id"] for ap in approvals])
    for ap in approvals:
        job = ids["job_openings"].get(ap.get("job_id"))
        if not job:
            skipped.append(f"approval {ap['id'][:8]}… — job not migrated")
            continue
        data = _pick(ap, "action", "actor_name", "comment", "created_at")
        data["job_id"] = job
        mirror.write("job_approvals", ap["id"], data)

    team = orbit.rows("job_hiring_team")
    mirror.source("hiring_team", [ht["id"] for ht in team])
    for ht in team:
        job = ids["job_openings"].get(ht.get("job_id"))
        if not job:
            skipped.append(f"hiring_team {ht['id'][:8]}… — job not migrated")
            continue
        data = _pick(ht, "role", "created_at")
        data["job_id"] = job
        person_ref(data, "person_id", ht.get("team_member_id"), people)
        data["notify_on_application"] = bool(ht.get("notify_on_application"))
        mirror.write("hiring_team", ht["id"], data)

    jd = orbit.rows("job_description_templates")
    mirror.source("jd_templates", [t["id"] for t in jd])
    for t in jd:
        data = _pick(
            t,
            "name",
            "department",
            "description_html",
            "requirements_html",
            "benefits_html",
            "about_html",
        )
        mirror.write("jd_templates", t["id"], data)

    careers = orbit.rows("careers_page_settings")
    mirror.source("careers_settings", [str(cs["id"]) for cs in careers])
    for cs in careers:
        data = _pick(
            cs,
            "headline",
            "intro_html",
            "logo_url",
            "hero_image_url",
            "contact_email",
            "footer_html",
        )
        mirror.write("careers_settings", str(cs["id"]), data)

    # Documents carry their storage path; the BYTES are copied by
    # migrate_orbit_files.py into app_files. Once a CV has been copied its
    # file_id is set here too, so a refresh keeps the link.
    docs = orbit.rows("candidate_documents")
    mirror.source("candidate_documents", [d["id"] for d in docs])
    cv_linked = 0
    for d in docs:
        candidate = ids["candidates"].get(d.get("candidate_id"))
        application = ids["applications"].get(d.get("application_id"))
        if not candidate and not application:
            skipped.append(
                f"document {d['id'][:8]}… — neither candidate nor application migrated"
            )
            continue
        data = _pick(
            d,
            "doc_type",
            "file_name",
            "storage_path",
            "mime_type",
            "size_bytes",
            "created_at",
        )
        if candidate:
            data["candidate_id"] = candidate
        if application:
            data["application_id"] = application
        person_ref(data, "uploaded_by", d.get("uploaded_by"), people)
        if d.get("storage_path"):
            hit = copied.get(storage_ref(CV_BUCKET, d["storage_path"]))
            if hit:
                data["file_id"] = hit["id"]
                cv_linked += 1
        mirror.write("candidate_documents", d["id"], data)
    notes["candidate_documents"] = f"{cv_linked} linked to a copied file"

    emails = orbit.rows("hiring_emails")
    mirror.source("hiring_emails", [e["id"] for e in emails])
    for e in emails:
        candidate = ids["candidates"].get(e.get("candidate_id"))
        application = ids["applications"].get(e.get("application_id"))
        data = _pick(
            e,
            "direction",
            "to_email",
            "from_email",
            "subject",
            "body_html",
            "status",
            "error",
            "sent_by_name",
            "created_at",
        )
        if candidate:
            data["candidate_id"] = candidate
        if application:
            data["application_id"] = application
        if ids["email_templates"].get(e.get("template_id")):
            data["template_id"] = ids["email_templates"][e["template_id"]]
        person_ref(data, "sent_by", e.get("sent_by"), people)
        mirror.write("hiring_emails", e["id"], data)

    if skipped:
        log(f"\n  {len(skipped)} row(s) not carried:")
        for line in skipped[:40]:
            log(f"    ! {line}")
        if len(skipped) > 40:
            log(f"    … and {len(skipped) - 40} more")
    code = finish(
        db,
        mirror,
        dry_run=dry_run,
        allow_mass_delete=allow_mass_delete,
        log=log,
        notes=notes,
    )
    return RunResult(
        code=code, stats=mirror.plain_stats(), skipped=skipped, org_name=org.name
    )


def main(argv: list[str] | None = None, *, orbit=None, db=None) -> int:
    """CLI entry. ``orbit``/``db`` are injectable so tests run with no network
    and inside their own transaction."""
    args = build_parser("Mirror Orbit's hiring data into Norm").parse_args(argv)
    owns_db = db is None
    if owns_db:
        # Progress lines must reach a log file as they happen, not at exit.
        sys.stdout.reconfigure(line_buffering=True)
        from app.db.engine import SessionLocal

        db = SessionLocal()
    try:
        result = run(
            db,
            orbit or Orbit(),
            org_email=args.org_email,
            org_id=args.org_id,
            expect_org=args.expect_org,
            dry_run=args.dry_run,
            allow_mass_delete=args.allow_mass_delete,
        )
        if owns_db and (result.code or args.dry_run):
            db.rollback()
        return result.code
    finally:
        if owns_db:
            db.close()


if __name__ == "__main__":
    raise SystemExit(main())
