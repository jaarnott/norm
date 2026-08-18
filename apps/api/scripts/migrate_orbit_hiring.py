"""Migrate Orbit's hiring data into the Norm Hiring app's storage.

Same contract as the training migration: read-only against Orbit, idempotent
(keyed by ``data->>'orbit_id'``), and writing into the ``hr_suite`` namespace
the two apps share — so a candidate hired here becomes a person the Training
app can enrol without anything being copied between them.

Small by comparison — hiring has barely started in Orbit — but the pipeline
shape is what matters, and it has one rule worth carrying carefully: an
application's ``status`` is DERIVED from its stage's ``stage_type``, never from
the stage's name, which users rename freely.

    uv run python scripts/migrate_orbit_hiring.py --org-email admin@norm.local --dry-run
    uv run python scripts/migrate_orbit_hiring.py --org-email admin@norm.local
"""

from __future__ import annotations

import argparse
import pathlib
import sys
import uuid

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from scripts.migrate_orbit_training import (  # noqa: E402 — path set above
    NAMESPACE,
    VENUE_ALIASES,
    Orbit,
    _pick,
)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--org-email", required=True)
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    from app.db.engine import SessionLocal
    from app.db.models import AppRecord, OrganizationMembership, User, Venue

    orbit = Orbit()
    db = SessionLocal()
    try:
        user = db.query(User).filter(User.email == args.org_email).first()
        membership = (
            db.query(OrganizationMembership)
            .filter(OrganizationMembership.user_id == user.id)
            .first()
            if user
            else None
        )
        if not membership:
            print(f"no org membership for {args.org_email}")
            return 1
        org_id = membership.organization_id

        norm_venues = {
            v.name: v.id
            for v in db.query(Venue).filter(Venue.organization_id == org_id).all()
        }
        venue_map = {}
        for v in orbit.rows("venues", "id,name"):
            target = VENUE_ALIASES.get(v["name"], v["name"])
            if target in norm_venues:
                venue_map[v["id"]] = norm_venues[target]

        existing = {}
        people_by_orbit = {}
        for row in (
            db.query(AppRecord)
            .filter(
                AppRecord.namespace == NAMESPACE, AppRecord.organization_id == org_id
            )
            .all()
        ):
            orbit_id = (row.data or {}).get("orbit_id")
            if orbit_id:
                existing[(row.collection, orbit_id)] = row
                if row.collection == "people":
                    people_by_orbit[orbit_id] = row.id

        stats: dict[str, dict[str, int]] = {}
        ids: dict[str, dict[str, str]] = {}
        #: Rows deliberately not migrated, and why. A migration that drops
        #: something MUST say so — a silent skip reads as "there was nothing
        #: there", which is the one thing a reconciliation must never let you
        #: believe.
        skipped: list[str] = []

        def write(collection, orbit_id, data, venue_id=None):
            data = {**data, "orbit_id": orbit_id}
            row = existing.get((collection, orbit_id))
            bucket = stats.setdefault(collection, {"new": 0, "updated": 0})
            if row is None:
                row = AppRecord(
                    id=str(uuid.uuid4()),
                    namespace=NAMESPACE,
                    organization_id=org_id,
                    venue_id=venue_id,
                    collection=collection,
                    data=data,
                    created_by=user.id,
                    updated_by=user.id,
                )
                if not args.dry_run:
                    db.add(row)
                existing[(collection, orbit_id)] = row
                bucket["new"] += 1
            else:
                row.data = data
                row.venue_id = venue_id
                bucket["updated"] += 1
            ids.setdefault(collection, {})[orbit_id] = row.id
            return row.id

        print("reading Orbit…")
        for j in orbit.rows("job_openings"):
            data = _pick(
                j,
                "title",
                "slug",
                "department",
                "employment_type",
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
            )
            data["is_published"] = bool(j.get("is_published"))
            write("job_openings", j["id"], data, venue_map.get(j.get("venue_id")))

        stage_types = {}
        for s in orbit.rows("job_pipeline_stages"):
            job = ids.get("job_openings", {}).get(s["job_id"])
            if not job:
                continue
            data = _pick(s, "name", "stage_type", "sort_index")
            data["job_id"] = job
            data.setdefault("stage_type", "active")
            stage_types[s["id"]] = data["stage_type"]
            write("pipeline_stages", s["id"], data)

        for f in orbit.rows("job_application_fields"):
            job = ids.get("job_openings", {}).get(f["job_id"])
            if not job:
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
            for flag in (
                "is_required",
                "is_enabled",
                "is_standard",
                "knockout_enabled",
            ):
                data[flag] = bool(f.get(flag))
            write("application_fields", f["id"], data)

        for c in orbit.rows("candidates"):
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
            )
            data["in_talent_pool"] = bool(c.get("in_talent_pool"))
            write("candidates", c["id"], data)

        for a in orbit.rows("candidate_applications"):
            job = ids.get("job_openings", {}).get(a["job_id"])
            candidate = ids.get("candidates", {}).get(a["candidate_id"])
            if not job or not candidate:
                why = (
                    "no job_id in Orbit" if not a.get("job_id") else "job not migrated"
                )
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
                "cover_letter",
            )
            data["job_id"] = job
            data["candidate_id"] = candidate
            data["stage_id"] = ids.get("pipeline_stages", {}).get(a.get("stage_id"))
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
            if a.get("team_member_id") and a["team_member_id"] in people_by_orbit:
                data["person_id"] = people_by_orbit[a["team_member_id"]]
            write("applications", a["id"], data)

        for r in orbit.rows("candidate_application_answers"):
            application = ids.get("applications", {}).get(r["application_id"])
            if not application:
                continue
            data = _pick(r, "field_key", "label", "value")
            data["application_id"] = application
            write("answers", r["id"], data)

        for n in orbit.rows("candidate_notes"):
            application = ids.get("applications", {}).get(n.get("application_id"))
            if not application:
                continue
            data = _pick(n, "body", "author_name", "created_at")
            data["application_id"] = application
            data["author"] = n.get("author_name")
            data["at"] = n.get("created_at")
            write("notes", n["id"], data)

        for a in orbit.rows("candidate_activity"):
            application = ids.get("applications", {}).get(a.get("application_id"))
            if not application:
                continue
            data = _pick(a, "activity_type", "summary", "actor_name")
            data["application_id"] = application
            data["actor"] = a.get("actor_name") or "Orbit"
            data["at"] = a.get("created_at")
            write("activity", a["id"], data)

        for i in orbit.rows("interviews"):
            application = ids.get("applications", {}).get(i.get("application_id"))
            if not application:
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
            )
            data["application_id"] = application
            write("interviews", i["id"], data)

        print()
        print(f"{'collection':24} {'new':>6} {'updated':>8}")
        for collection, counts in stats.items():
            print(f"  {collection:22} {counts['new']:>6} {counts['updated']:>8}")
        if skipped:
            print(f"\n  {len(skipped)} row(s) skipped:")
            for line in skipped:
                print(f"    ! {line}")

        if args.dry_run:
            db.rollback()
            print("\n--dry-run: nothing written")
            return 0
        db.commit()
        print("\nmigrated")
        return 0
    finally:
        db.close()


if __name__ == "__main__":
    raise SystemExit(main())
