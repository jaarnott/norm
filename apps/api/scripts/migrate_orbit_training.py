"""Migrate Orbit's training data into the Norm Training app's storage.

Reads Orbit's Supabase directly over PostgREST and writes ``app_records`` in the
``hr_suite`` namespace. **Read-only against Orbit** — nothing here writes back,
so it can be run against a live system as often as you like.

Why not through Orbit's MCP connector, which Norm already has: all 18 of its
training programs are group-wide (``venue_id IS NULL``) and its API filters
programs by ``venue_id IN (...)``, so that surface returns **zero** programs and
**zero** of the 580 assignments. Migrating through it would have looked like a
clean run over an empty dataset.

Idempotent. Every record carries the Orbit uuid it came from
(``data->>'orbit_id'``), and a re-run updates in place rather than duplicating —
so this can be run now as a seed and again at cutover to pick up whatever
changed in between.

    uv run python scripts/migrate_orbit_training.py --org-email admin@norm.local --dry-run
    uv run python scripts/migrate_orbit_training.py --org-email admin@norm.local

Credentials come from ``/workspaces/norm/.local/orbit-supabase.json`` (git-
ignored). They are never printed, and neither is any row's content.
"""

from __future__ import annotations

import argparse
import json
import pathlib
import sys
import urllib.request
import uuid

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

CREDS = pathlib.Path("/workspaces/norm/.local/orbit-supabase.json")
NAMESPACE = "hr_suite"

#: Orbit venue name -> Norm venue name, where they differ. Anything unmapped
#: becomes a group-wide row rather than being dropped or guessed at.
VENUE_ALIASES = {"Bessie": "Bessie & Royals"}


class Orbit:
    """A read-only PostgREST client. Only ever issues GETs."""

    def __init__(self) -> None:
        cfg = json.loads(CREDS.read_text())
        key = cfg.get("service_role_key") or ""
        if not key:
            raise SystemExit(f"no service_role_key in {CREDS}")
        self.base = cfg["url"].rstrip("/") + "/rest/v1"
        self.headers = {"apikey": key, "Authorization": f"Bearer {key}"}

    def rows(self, table: str, select: str = "*", page: int = 1000) -> list[dict]:
        out: list[dict] = []
        start = 0
        while True:
            req = urllib.request.Request(f"{self.base}/{table}?select={select}")
            for k, v in self.headers.items():
                req.add_header(k, v)
            req.add_header("Range", f"{start}-{start + page - 1}")
            with urllib.request.urlopen(req, timeout=120) as resp:
                batch = json.loads(resp.read().decode())
            out.extend(batch)
            if len(batch) < page:
                return out
            start += page


def _pick(row: dict, *keys: str) -> dict:
    """The named fields, dropping the ones Orbit left null — a record should
    not be full of explicit nulls just because the source column exists."""
    return {k: row[k] for k in keys if row.get(k) is not None}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument(
        "--org-email", required=True, help="a member; their org receives the data"
    )
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    from app.db.engine import SessionLocal
    from app.db.models import AppRecord, OrganizationMembership, User, Venue

    orbit = Orbit()
    db = SessionLocal()
    try:
        user = db.query(User).filter(User.email == args.org_email).first()
        if not user:
            print(f"no user {args.org_email}")
            return 1
        membership = (
            db.query(OrganizationMembership)
            .filter(OrganizationMembership.user_id == user.id)
            .first()
        )
        if not membership:
            print(f"{args.org_email} has no organization membership")
            return 1
        org_id = membership.organization_id

        # ---- venue map ----------------------------------------------------
        norm_venues = {
            v.name: v.id
            for v in db.query(Venue).filter(Venue.organization_id == org_id).all()
        }
        venue_map: dict[str, str] = {}
        unmapped: list[str] = []
        for v in orbit.rows("venues", "id,name"):
            target = VENUE_ALIASES.get(v["name"], v["name"])
            if target in norm_venues:
                venue_map[v["id"]] = norm_venues[target]
            else:
                unmapped.append(v["name"])
        if unmapped:
            # Not fatal: a row scoped to a venue Norm does not have becomes
            # group-wide, which is visible to everyone rather than lost.
            print(
                f"  ! no Norm venue for: {', '.join(sorted(unmapped))} → those rows land group-wide"
            )

        # ---- existing records, so a re-run updates instead of duplicating --
        existing: dict[tuple[str, str], AppRecord] = {}
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

        stats: dict[str, dict[str, int]] = {}
        # orbit uuid -> the Norm record id it became, per collection
        ids: dict[str, dict[str, str]] = {}

        def write(
            collection: str, orbit_id: str, data: dict, venue_id: str | None = None
        ) -> str:
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
                row.updated_by = user.id
                bucket["updated"] += 1
            ids.setdefault(collection, {})[orbit_id] = row.id
            return row.id

        def norm_venue(orbit_venue_id):
            return venue_map.get(orbit_venue_id) if orbit_venue_id else None

        # ---- people --------------------------------------------------------
        # 4,093 team_members exist but only 121 are active. Migrating all of
        # them would bury the 208 who matter in four thousand rows of former
        # staff, so this takes the active ones PLUS anyone training history
        # hangs off — dropping those would orphan their completions.
        print("reading Orbit…")
        members = orbit.rows(
            "team_members",
            "id,name,email,role,pin,is_active,loadedhub_id,loadedhub_default_role_name",
        )
        member_venues = orbit.rows("team_member_venues", "team_member_id,venue_id")
        assignments = orbit.rows("training_assignments")
        plans = orbit.rows("training_plans")

        referenced = {a["team_member_id"] for a in assignments}
        referenced |= {p["team_member_id"] for p in plans}
        venues_by_member: dict[str, list[str]] = {}
        for mv in member_venues:
            mapped = norm_venue(mv.get("venue_id"))
            if mapped:
                venues_by_member.setdefault(mv["team_member_id"], []).append(mapped)

        norm_users = {u.email.lower(): u.id for u in db.query(User).all() if u.email}
        linked = 0
        for m in members:
            if not (m.get("is_active") or m["id"] in referenced):
                continue
            data = _pick(m, "name", "email", "role", "loadedhub_id")
            data["is_active"] = bool(m.get("is_active"))
            if m.get("loadedhub_default_role_name"):
                data["loadedhub_role"] = m["loadedhub_default_role_name"]
            venues = venues_by_member.get(m["id"])
            if venues:
                data["venues"] = venues
            # Link to a Norm login only where an email matches exactly. Nobody
            # needs a Norm account to be trained; this is a convenience, not
            # the identity.
            norm_user = norm_users.get(str(m.get("email") or "").lower())
            if norm_user:
                data["norm_user_id"] = norm_user
                linked += 1
            write("people", m["id"], data)

        # ---- the program tree ---------------------------------------------
        for p in orbit.rows("training_programs"):
            data = _pick(
                p,
                "name",
                "description",
                "icon",
                "default_due_days",
                "sort_index",
                "variants",
            )
            data["is_active"] = bool(p.get("is_active"))
            data["requires_plan"] = bool(p.get("requires_plan"))
            if p.get("auto_enroll_filter"):
                data["auto_enroll_filter"] = p["auto_enroll_filter"]
            write("programs", p["id"], data, norm_venue(p.get("venue_id")))

        for m in orbit.rows("training_modules"):
            parent = ids["programs"].get(m["program_id"])
            if not parent:
                continue
            data = _pick(m, "name", "description", "sort_index")
            data["program_id"] = parent
            data["requires_signoff"] = bool(m.get("requires_signoff"))
            write("modules", m["id"], data)

        for s in orbit.rows("training_sections"):
            parent = ids["modules"].get(s["module_id"])
            if not parent:
                continue
            data = _pick(
                s,
                "name",
                "section_type",
                "introduction",
                "instructions",
                "trainer_time_minutes",
                "trainee_time_minutes",
                "sort_index",
            )
            data["module_id"] = parent
            write("sections", s["id"], data)

        for c in orbit.rows("training_content"):
            section = ids["sections"].get(c.get("section_id"))
            if not section:
                # Orbit has content rows that predate sections; without a
                # section they are unreachable in the tree and would only
                # inflate progress denominators.
                continue
            data = _pick(c, "title", "content_type", "sort_index")
            data["section_id"] = section
            data["module_id"] = ids["modules"].get(c.get("module_id"))
            if c.get("content") not in (None, {}):
                data["body"] = c["content"]
            write("content", c["id"], data)

        # ---- enrolments and progress ---------------------------------------
        for a in assignments:
            program = ids["programs"].get(a["program_id"])
            person = ids["people"].get(a["team_member_id"])
            if not program or not person:
                continue
            data = _pick(
                a,
                "status",
                "due_date",
                "assigned_at",
                "completed_at",
                "variant_id",
                "variant_name",
            )
            data["program_id"] = program
            data["person_id"] = person
            data.setdefault("status", "assigned")
            write("assignments", a["id"], data, norm_venue(a.get("venue_id")))

        for c in orbit.rows("training_content_completions"):
            assignment = ids["assignments"].get(c["assignment_id"])
            content = ids["content"].get(c["content_id"])
            if not assignment or not content:
                continue
            data = {"assignment_id": assignment, "content_id": content}
            if c.get("completed_at"):
                data["completed_at"] = c["completed_at"]
            # Orbit keeps the sign-off state inside a `result` blob; the app
            # reads flat keys, and this is the rule the whole tracker turns on
            # — flatten it here rather than teach every reader the blob.
            result = c.get("result") or {}
            for key in ("awaiting_signoff", "signoff_at", "rejected", "rejected_at"):
                if result.get(key) is not None:
                    data[key] = result[key]
            if result:
                data["result"] = result
            write("completions", c["id"], data)

        for p in plans:
            program = ids["programs"].get(p["program_id"])
            person = ids["people"].get(p["team_member_id"])
            if not program or not person:
                continue
            data = _pick(p, "status", "variant_id", "variant_name", "created_at")
            data["program_id"] = program
            data["person_id"] = person
            data.setdefault("status", "active")
            write("plans", p["id"], data, norm_venue(p.get("venue_id")))

        for ps in orbit.rows("training_plan_sections"):
            plan = ids["plans"].get(ps["plan_id"])
            section = ids["sections"].get(ps["section_id"])
            if not plan or not section:
                continue
            data = _pick(ps, "due_date", "start_time", "status", "completed_at")
            data["plan_id"] = plan
            data["section_id"] = section
            if ps.get("trainer_id"):
                data["trainer_person_id"] = ids["people"].get(ps["trainer_id"])
            write("plan_sections", ps["id"], data)

        # ---- capability frameworks -----------------------------------------
        categories = orbit.rows("capability_categories")
        capabilities = orbit.rows("capabilities")
        caps_by_category: dict[str, list[dict]] = {}
        for cap in capabilities:
            caps_by_category.setdefault(cap["category_id"], []).append(
                _pick(
                    cap,
                    "name",
                    "level1_descriptor",
                    "level2_descriptor",
                    "level3_descriptor",
                    "sort_index",
                )
            )
        cats_by_framework: dict[str, list[dict]] = {}
        for cat in categories:
            cats_by_framework.setdefault(cat["framework_id"], []).append(
                {
                    **_pick(cat, "name", "sort_index"),
                    "capabilities": sorted(
                        caps_by_category.get(cat["id"], []),
                        key=lambda c: c.get("sort_index") or 0,
                    ),
                }
            )
        for f in orbit.rows("capability_frameworks"):
            data = _pick(f, "name", "role_label", "baseline_prerequisites")
            data["is_active"] = bool(f.get("is_active"))
            # Nested rather than three collections: a framework is read whole,
            # and its categories and capabilities have no life of their own.
            data["categories"] = sorted(
                cats_by_framework.get(f["id"], []),
                key=lambda c: c.get("sort_index") or 0,
            )
            write("capability_frameworks", f["id"], data)

        # ---- reconcile ------------------------------------------------------
        print()
        print(f"{'collection':24} {'new':>6} {'updated':>8}   source")
        source_counts = {
            "people": f"{len(members)} members ({len(referenced)} with training)",
            "programs": len(orbit.rows("training_programs", "id")),
            "modules": len(orbit.rows("training_modules", "id")),
            "sections": len(orbit.rows("training_sections", "id")),
            "content": len(orbit.rows("training_content", "id")),
            "assignments": len(assignments),
            "completions": len(orbit.rows("training_content_completions", "id")),
            "plans": len(plans),
            "plan_sections": len(orbit.rows("training_plan_sections", "id")),
            "capability_frameworks": len(orbit.rows("capability_frameworks", "id")),
        }
        for collection, counts in stats.items():
            print(
                f"  {collection:22} {counts['new']:>6} {counts['updated']:>8}   "
                f"of {source_counts.get(collection, '?')}"
            )
        print(f"\n  {linked} people linked to an existing Norm login by email")

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
