"""Training — server-side logic.

The UI is a sandboxed iframe that can only talk to this through
``norm.run({op: ...})``, so every query that needs to join across collections
lives here. Storage has deliberately small query support (equality on a
top-level key, one venue filter) — joining is the caller's job, and doing it
here means one round trip instead of the UI fetching six collections and
stitching them together over postMessage.

Three rules ported from Orbit deliberately, because a naive rebuild loses them:

1. **An enrolment is an instance**, keyed ``(program, variant, venue)`` — not a
   program. One person can hold several assignments for one program, one per
   variant/venue they were trained on, each tracking its own progress.

2. **A program may be group-wide** (no venue). Orbit's own API filters programs
   by ``venue_id IN (...)``, so its global programs — which is most of them —
   are invisible through it, and every assignment hanging off one vanishes too.
   Norm's storage returns global rows alongside a venue's own, and nothing here
   re-introduces that filter.

3. **Effective completion**: a completion does not count while it is awaiting
   sign-off, nor if it was rejected. Orbit applies this rule identically in
   three places; here it is ``_counts()``, used everywhere.
"""


def _now():
    return datetime.datetime.now(datetime.timezone.utc).isoformat()


def _index(rows, key="id"):
    return {r.get(key): r for r in rows}


def _group(rows, key):
    out = {}
    for r in rows:
        out.setdefault(r.get(key), []).append(r)
    return out


def _counts(completion):
    """Does this completion count toward progress?

    Awaiting a sign-off that has not happened, or explicitly rejected, means
    the trainee has done the work but it is not yet accepted.
    """
    if completion.get("awaiting_signoff") and not completion.get("signoff_at"):
        return False
    return not completion.get("rejected")


def _instance_key(row):
    return (
        row.get("program_id"),
        row.get("variant_id") or "",
        row.get("venue_id") or "",
    )


def _instance_label(row, venue_names):
    """'Larder · Glass Goose' — the variant and venue that make one enrolment
    distinct from another on the same program."""
    parts = [row.get("variant_name"), venue_names.get(row.get("venue_id"))]
    return " · ".join([p for p in parts if p])


#: Storage adds these around a record; they must never be written back INTO
#: it, or an update would bury a copy of the envelope in the data.
_META = ("id", "venue_id", "created_at", "updated_at")

_SECTION_LABELS = {
    "online": "Online learning",
    "on_shift": "On-shift training",
    "shift_exercise": "Shift exercise",
}


def _delete_section_tree(section_id):
    """A section, its content, and any SCHEDULED plan rows pointing at it.

    Storage has no foreign keys, so nothing cascades on its own. Orbit deletes
    the content and the plan rows too — worth keeping, because a plan row whose
    section no longer exists is a due date nobody can ever complete.
    """
    for c in store.list("content", where={"section_id": section_id}):
        store.delete("content", c["id"])
    for ps in store.list("plan_sections", where={"section_id": section_id}):
        store.delete("plan_sections", ps["id"])
    store.delete("sections", section_id)


def _delete_module_tree(module_id):
    for s in store.list("sections", where={"module_id": module_id}):
        _delete_section_tree(s["id"])
    store.delete("modules", module_id)


def run(params, call_api, log):
    op = (params or {}).get("op") or "overview"
    venues = {v.get("id"): v.get("name") for v in (params or {}).get("venues") or []}

    if op == "overview":
        programs = store.list("programs")
        modules = store.list("modules")
        assignments = store.list("assignments")
        by_program = _group(modules, "program_id")
        enrolled = _group(assignments, "program_id")
        rows = []
        for p in programs:
            a = enrolled.get(p["id"], [])
            rows.append(
                {
                    **p,
                    "module_count": len(by_program.get(p["id"], [])),
                    "assignment_count": len(a),
                    "completed_count": sum(
                        1 for x in a if x.get("status") == "completed"
                    ),
                }
            )
        rows.sort(
            key=lambda r: (not r.get("is_active", True), str(r.get("name") or ""))
        )
        return {"programs": rows}

    if op == "program":
        program_id = params.get("program_id")
        program = store.get("programs", program_id)
        modules = sorted(
            [m for m in store.list("modules", where={"program_id": program_id})],
            key=lambda m: m.get("sort_index") or 0,
        )
        module_ids = {m["id"] for m in modules}
        sections = sorted(
            [s for s in store.list("sections") if s.get("module_id") in module_ids],
            key=lambda s: s.get("sort_index") or 0,
        )
        section_ids = {s["id"] for s in sections}
        content = sorted(
            [c for c in store.list("content") if c.get("section_id") in section_ids],
            key=lambda c: c.get("sort_index") or 0,
        )
        return {
            "program": program,
            "modules": modules,
            "sections": sections,
            "content": content,
        }

    if op == "enrollments":
        program_id = params.get("program_id")
        assignments = store.list("assignments", where={"program_id": program_id})
        people = _index(store.list("people"))
        rows = []
        for a in assignments:
            person = people.get(a.get("person_id")) or {}
            rows.append(
                {
                    **a,
                    "person_name": person.get("name") or "(unknown)",
                    "person_role": person.get("role"),
                    "instance": _instance_label(a, venues),
                }
            )
        rows.sort(key=lambda r: str(r.get("person_name")))
        # Everyone who is NOT already on this exact instance — the enrol list
        # has to be per-instance, or a second variant could never be added.
        variant_id = params.get("variant_id") or ""
        venue_id = params.get("venue_id") or ""
        taken = {
            a.get("person_id")
            for a in assignments
            if (a.get("variant_id") or "") == variant_id
            and (a.get("venue_id") or "") == venue_id
        }
        available = [
            {"id": p["id"], "name": p.get("name"), "role": p.get("role")}
            for p in people.values()
            if p["id"] not in taken and p.get("is_active", True)
        ]
        available.sort(key=lambda p: str(p.get("name")))
        return {"assignments": rows, "available": available}

    if op == "tracker":
        """The member × program matrix.

        A cell is (person, program). It holds one entry per assignment, plus
        one per active plan NOT already covered by an assignment on the same
        (variant, venue) — so a person trained on two variants shows two
        badges, and a scheduled plan does not duplicate an enrolment.
        """
        people = store.list("people")
        programs = _index(store.list("programs"))
        assignments = store.list("assignments")
        plans = store.list("plans")
        modules = store.list("modules")
        sections = _index(store.list("sections"))
        content = store.list("content")
        completions = store.list("completions")
        plan_sections = store.list("plan_sections")

        modules_by_program = _group(modules, "program_id")
        sections_by_module = _group([s for s in sections.values()], "module_id")
        content_by_section = _group(content, "section_id")
        done_by_assignment = {}
        for c in completions:
            if _counts(c):
                done_by_assignment.setdefault(c.get("assignment_id"), set()).add(
                    c.get("content_id")
                )
        plan_sections_by_plan = _group(plan_sections, "plan_id")

        def assignment_cell(a):
            mods = sorted(
                modules_by_program.get(a.get("program_id"), []),
                key=lambda m: m.get("sort_index") or 0,
            )
            done = done_by_assignment.get(a["id"], set())
            total_items = 0
            first_incomplete = None
            for m in mods:
                items = []
                for s in sections_by_module.get(m["id"], []):
                    items.extend(content_by_section.get(s["id"], []))
                if not items:
                    continue
                total_items += len(items)
                if first_incomplete is None and any(c["id"] not in done for c in items):
                    first_incomplete = m.get("name")
            if total_items == 0:
                # Structure with nothing in it is not "complete" — it is a
                # program nobody can finish. Orbit reads this as a dash too.
                return {"status": "none"}
            if first_incomplete:
                return {"status": "progress", "label": first_incomplete}
            return {"status": "complete"}

        def plan_cell(p):
            # The plan's OWN status wins when it says completed. Orbit never
            # wrote section status back — 94 of its 110 completed plans still
            # have every section 'pending' — so deriving purely from sections
            # would report finished training as outstanding for most people.
            # Sections still drive the detail when the plan is not yet done.
            rows = plan_sections_by_plan.get(p["id"], [])
            if p.get("status") == "completed":
                return {"status": "complete"}
            if not rows:
                return {"status": "none"}
            pending = [r for r in rows if r.get("status") != "completed"]
            if not pending:
                return {"status": "complete"}
            pending.sort(key=lambda r: str(r.get("due_date") or "9999"))
            nxt = sections.get(pending[0].get("section_id")) or {}
            # Orbit renders the section name verbatim, so an unnamed section
            # produces a badge with no text at all. Say something instead.
            return {"status": "progress", "label": nxt.get("name") or "In progress"}

        covered = {_instance_key(a) for a in assignments}
        cells = {}
        seen_programs = {}
        for a in assignments:
            program = programs.get(a.get("program_id"))
            if not program:
                continue
            seen_programs[program["id"]] = program
            cell = assignment_cell(a)
            cell["instance"] = _instance_label(a, venues)
            cells.setdefault(f"{a.get('person_id')}:{program['id']}", []).append(cell)
        for p in plans:
            if p.get("status") != "active" or _instance_key(p) in covered:
                continue
            program = programs.get(p.get("program_id"))
            if not program:
                continue
            seen_programs[program["id"]] = program
            cell = plan_cell(p)
            cell["instance"] = _instance_label(p, venues)
            cell["scheduled"] = True
            cells.setdefault(f"{p.get('person_id')}:{program['id']}", []).append(cell)

        columns = sorted(seen_programs.values(), key=lambda p: str(p.get("name") or ""))
        rows = []
        for person in sorted(people, key=lambda p: str(p.get("name") or "")):
            row_cells = [cells.get(f"{person['id']}:{c['id']}", []) for c in columns]
            if not any(row_cells):
                continue
            rows.append({"person": person, "cells": row_cells})
        return {"columns": columns, "rows": rows}

    # ---- authoring ------------------------------------------------------
    #
    # Every mutation lives here rather than in the UI because two of them are
    # not single writes: storage has no foreign keys, so a delete has to walk
    # its own children, and an ordering change has to renumber siblings. Doing
    # that from the iframe would mean a half-finished tree whenever a call
    # failed midway.

    if op == "save_program":
        fields = params.get("fields") or {}
        program_id = params.get("program_id")
        allowed = (
            "name",
            "description",
            "icon",
            "is_active",
            "requires_plan",
            "default_due_days",
            "variants",
        )
        if program_id:
            current = store.get("programs", program_id)
            data = {k: v for k, v in current.items() if k not in _META}
        else:
            data = {"is_active": True}
        for key in allowed:
            if key in fields:
                data[key] = fields[key]
        if not str(data.get("name") or "").strip():
            return {"error": "a program needs a name"}
        saved = store.put("programs", data, program_id)
        return {"program": saved}

    if op == "delete_program":
        program_id = params.get("program_id")
        modules = store.list("modules", where={"program_id": program_id})
        for m in modules:
            _delete_module_tree(m["id"])
        for a in store.list("assignments", where={"program_id": program_id}):
            for c in store.list("completions", where={"assignment_id": a["id"]}):
                store.delete("completions", c["id"])
            store.delete("assignments", a["id"])
        for pl in store.list("plans", where={"program_id": program_id}):
            for ps in store.list("plan_sections", where={"plan_id": pl["id"]}):
                store.delete("plan_sections", ps["id"])
            store.delete("plans", pl["id"])
        store.delete("programs", program_id)
        return {"ok": True}

    if op == "set_assignment_status":
        """Mark an enrolment complete, or reopen it.

        Server-side rather than a read-modify-write from the browser, so two
        managers acting on the same assignment cannot silently lose one edit —
        and because in an admin-only build this is the ONLY way anything ever
        reaches 'complete'. The trainee experience is what moves it in the
        source system.
        """
        assignment_id = params.get("assignment_id")
        status = params.get("status") or "assigned"
        if status not in ("assigned", "in_progress", "completed"):
            return {"error": f"'{status}' is not an assignment status"}
        current = store.get("assignments", assignment_id)
        data = {k: v for k, v in current.items() if k not in _META}
        data["status"] = status
        data["completed_at"] = _now() if status == "completed" else None
        return {"assignment": store.put("assignments", data, assignment_id)}

    if op == "add_module":
        program_id = params.get("program_id")
        existing = store.list("modules", where={"program_id": program_id})
        return {
            "module": store.put(
                "modules",
                {
                    "program_id": program_id,
                    "name": params.get("name") or "New module",
                    "requires_signoff": bool(params.get("requires_signoff")),
                    "sort_index": len(existing),
                },
            )
        }

    if op == "update_module":
        module_id = params.get("module_id")
        current = store.get("modules", module_id)
        data = {k: v for k, v in current.items() if k not in _META}
        for key in ("name", "description", "requires_signoff"):
            if key in (params.get("fields") or {}):
                data[key] = params["fields"][key]
        return {"module": store.put("modules", data, module_id)}

    if op == "delete_module":
        _delete_module_tree(params.get("module_id"))
        return {"ok": True}

    if op == "add_section":
        module_id = params.get("module_id")
        existing = store.list("sections", where={"module_id": module_id})
        section_type = params.get("section_type") or "online"
        section = store.put(
            "sections",
            {
                "module_id": module_id,
                "name": params.get("name") or _SECTION_LABELS.get(section_type),
                "section_type": section_type,
                "sort_index": len(existing),
            },
        )
        # An on-shift or exercise section is delivered in person and signed
        # off against uploaded evidence, so it needs one content item to hang
        # that evidence on. Without it the section has nothing to complete and
        # the tracker can never move past it.
        if section_type in ("on_shift", "shift_exercise"):
            store.put(
                "content",
                {
                    "section_id": section["id"],
                    "module_id": module_id,
                    "title": "Upload training evidence",
                    "content_type": "file_upload",
                    "requires_signoff": True,
                    "sort_index": 0,
                },
            )
        return {"section": section}

    if op == "update_section":
        section_id = params.get("section_id")
        current = store.get("sections", section_id)
        data = {k: v for k, v in current.items() if k not in _META}
        for key in (
            "name",
            "section_type",
            "introduction",
            "instructions",
            "trainer_time_minutes",
            "trainee_time_minutes",
        ):
            if key in (params.get("fields") or {}):
                data[key] = params["fields"][key]
        return {"section": store.put("sections", data, section_id)}

    if op == "delete_section":
        _delete_section_tree(params.get("section_id"))
        return {"ok": True}

    if op == "add_content":
        section_id = params.get("section_id")
        section = store.get("sections", section_id)
        existing = store.list("content", where={"section_id": section_id})
        return {
            "content": store.put(
                "content",
                {
                    "section_id": section_id,
                    "module_id": section.get("module_id"),
                    "title": params.get("title") or "Untitled",
                    "content_type": params.get("content_type") or "rich_text",
                    "body": params.get("body"),
                    "sort_index": len(existing),
                },
            )
        }

    if op == "update_content":
        content_id = params.get("content_id")
        current = store.get("content", content_id)
        data = {k: v for k, v in current.items() if k not in _META}
        for key in ("title", "content_type", "body"):
            if key in (params.get("fields") or {}):
                data[key] = params["fields"][key]
        return {"content": store.put("content", data, content_id)}

    if op == "delete_content":
        store.delete("content", params.get("content_id"))
        return {"ok": True}

    if op == "reorder":
        """Renumber siblings after a drag. The caller sends the whole new
        order, so a dropped call cannot leave two items claiming one slot."""
        collection = params.get("collection")
        if collection not in ("modules", "sections", "content"):
            return {"error": "only modules, sections and content are ordered"}
        for index, record_id in enumerate(params.get("ids") or []):
            current = store.get(collection, record_id)
            data = {k: v for k, v in current.items() if k not in _META}
            data["sort_index"] = index
            store.put(collection, data, record_id)
        return {"ok": True}

    return {"error": f"unknown op '{op}'"}
