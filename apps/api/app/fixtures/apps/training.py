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


def _today():
    # datetime.date.today() reaches for the time module via __import__, which
    # the sandbox blocks; datetime.now() (the pattern _now already uses) does not.
    return datetime.datetime.now(datetime.timezone.utc).date().isoformat()


def _add_days(iso_date, days):
    y, m, d = (int(x) for x in iso_date.split("-"))
    base = datetime.datetime(y, m, d, tzinfo=datetime.timezone.utc)
    return (base + datetime.timedelta(days=days)).date().isoformat()


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
            # The venue filter in the UI reads this; without it, filtering fell
            # back to the person's venue list and a venue-scoped enrolment for a
            # person with no venues array vanished under its own venue.
            cell["venue_id"] = a.get("venue_id")
            cells.setdefault(f"{a.get('person_id')}:{program['id']}", []).append(cell)
        for p in plans:
            # A completed plan still belongs on the tracker, as done — dropping
            # it would make a plan vanish the moment it is marked complete.
            # Only a cancelled plan, or one an assignment already covers, is
            # left out.
            if p.get("status") == "cancelled" or _instance_key(p) in covered:
                continue
            program = programs.get(p.get("program_id"))
            if not program:
                continue
            seen_programs[program["id"]] = program
            cell = plan_cell(p)
            cell["instance"] = _instance_label(p, venues)
            cell["scheduled"] = True
            cell["venue_id"] = p.get("venue_id")
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
        content = store.put(
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
        # Grandfather anyone already finished: a newly-added item must not drag
        # a completed enrolment back to incomplete. Orbit writes a completion
        # marked grandfathered; _counts() then counts it toward progress.
        module = (
            store.get("modules", section.get("module_id"))
            if section.get("module_id")
            else None
        )
        program_id = module.get("program_id") if module else None
        if program_id:
            for a in store.list("assignments", where={"program_id": program_id}):
                if a.get("status") == "completed":
                    store.put(
                        "completions",
                        {
                            "assignment_id": a["id"],
                            "content_id": content["id"],
                            "completed_at": _now(),
                            "result": {
                                "grandfathered": True,
                                "reason": "Added after this training was completed",
                            },
                        },
                    )
        return {"content": content}

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

    if op == "enrol":
        """Put one person on a program instance (program + variant + venue).

        Sets the due date the way Orbit's DB trigger did — today +
        default_due_days — but in visible app logic. A program with no
        default_due_days gets no due date, exactly as Orbit.
        """
        program_id = params.get("program_id")
        person_id = params.get("person_id")
        if not program_id or not person_id:
            return {"error": "a program and a person are required"}
        program = store.get("programs", program_id)
        variant_id = params.get("variant_id") or None
        venue_id = params.get("venue_id") or None
        # Guard the instance: one assignment per (person, program, variant, venue).
        existing = store.list("assignments", where={"program_id": program_id})
        for a in existing:
            if (
                a.get("person_id") == person_id
                and (a.get("variant_id") or None) == variant_id
                and (a.get("venue_id") or None) == venue_id
            ):
                return {"error": "already enrolled on this instance"}
        data = {
            "program_id": program_id,
            "person_id": person_id,
            "status": "assigned",
            "assigned_at": _now(),
            "variant_id": variant_id,
            "variant_name": params.get("variant_name"),
        }
        due_days = program.get("default_due_days")
        if due_days:
            data["due_date"] = _add_days(_today(), int(due_days))
        return {"assignment": store.put("assignments", data, venue_id=venue_id)}

    if op == "bulk_enrol":
        """Enrol several people on one instance in a single call. Skips anyone
        already on it rather than failing the whole batch."""
        program_id = params.get("program_id")
        program = store.get("programs", program_id)
        variant_id = params.get("variant_id") or None
        venue_id = params.get("venue_id") or None
        existing = store.list("assignments", where={"program_id": program_id})
        taken = {
            a.get("person_id")
            for a in existing
            if (a.get("variant_id") or None) == variant_id
            and (a.get("venue_id") or None) == venue_id
        }
        due_days = program.get("default_due_days")
        due = _add_days(_today(), int(due_days)) if due_days else None
        created = 0
        for person_id in params.get("person_ids") or []:
            if person_id in taken:
                continue
            data = {
                "program_id": program_id,
                "person_id": person_id,
                "status": "assigned",
                "assigned_at": _now(),
                "variant_id": variant_id,
                "variant_name": params.get("variant_name"),
            }
            if due:
                data["due_date"] = due
            store.put("assignments", data, venue_id=venue_id)
            created += 1
        return {"enrolled": created}

    if op == "unenrol":
        """Remove an enrolment and its completions — storage has no cascade."""
        assignment_id = params.get("assignment_id")
        for c in store.list("completions", where={"assignment_id": assignment_id}):
            store.delete("completions", c["id"])
        store.delete("assignments", assignment_id)
        return {"ok": True}

    if op == "content":
        """One content item, with its body — for the viewer."""
        return {"content": store.get("content", params.get("content_id"))}

    if op == "member":
        """One person's whole training picture: every assignment with its
        module-by-module progress, and every active plan with its sections."""
        person_id = params.get("person_id")
        person = store.get("people", person_id)
        programs = _index(store.list("programs"))
        modules = _group(store.list("modules"), "program_id")
        sections = _index(store.list("sections"))
        content = store.list("content")
        content_by_module = _group(content, "module_id")
        assignments = [
            a for a in store.list("assignments") if a.get("person_id") == person_id
        ]
        done_by_assignment = {}
        comps = store.list("completions")
        by_assignment = _group(comps, "assignment_id")
        rows = []
        for a in assignments:
            program = programs.get(a.get("program_id")) or {}
            done = {
                c.get("content_id")
                for c in by_assignment.get(a["id"], [])
                if _counts(c)
            }
            mod_rows = []
            total = complete = 0
            for m in sorted(
                modules.get(a.get("program_id"), []),
                key=lambda m: m.get("sort_index") or 0,
            ):
                items = content_by_module.get(m["id"], [])
                if not items:
                    continue
                d = sum(1 for c in items if c["id"] in done)
                total += len(items)
                complete += d
                mod_rows.append(
                    {
                        "name": m.get("name"),
                        "done": d,
                        "total": len(items),
                    }
                )
            rows.append(
                {
                    "assignment_id": a["id"],
                    "program": program.get("name"),
                    "instance": _instance_label(a, venues),
                    "status": a.get("status"),
                    "modules": mod_rows,
                    "percent": round(complete / total * 100) if total else 0,
                }
            )
        rows.sort(key=lambda r: str(r.get("program")))
        # Plans the member is on (active), with their sections.
        plans = [
            p
            for p in store.list("plans")
            if p.get("person_id") == person_id and p.get("status") == "active"
        ]
        plan_rows = []
        ps_by_plan = _group(store.list("plan_sections"), "plan_id")
        for pl in plans:
            program = programs.get(pl.get("program_id")) or {}
            secs = ps_by_plan.get(pl["id"], [])
            plan_rows.append(
                {
                    "plan_id": pl["id"],
                    "program": program.get("name"),
                    "sections": [
                        {
                            "name": (sections.get(x.get("section_id")) or {}).get(
                                "name"
                            )
                            or "Section",
                            "status": x.get("status"),
                            "due_date": x.get("due_date"),
                        }
                        for x in sorted(
                            secs, key=lambda x: str(x.get("due_date") or "")
                        )
                    ],
                }
            )
        return {
            "person": {
                "id": person["id"],
                "name": person.get("name"),
                "role": person.get("role"),
            },
            "assignments": rows,
            "plans": plan_rows,
        }

    if op == "plans":
        """Every plan, with the person and program named and the next due date."""
        status = params.get("status")
        plans = store.list("plans")
        if status and status != "all":
            plans = [p for p in plans if p.get("status") == status]
        people = _index(store.list("people"))
        programs = _index(store.list("programs"))
        ps_by_plan = _group(store.list("plan_sections"), "plan_id")
        rows = []
        for pl in plans:
            secs = ps_by_plan.get(pl["id"], [])
            pending = [s for s in secs if s.get("status") != "completed"]
            pending.sort(key=lambda s: str(s.get("due_date") or "9999"))
            rows.append(
                {
                    **pl,
                    "person_name": (people.get(pl.get("person_id")) or {}).get("name")
                    or "(unknown)",
                    "program_name": (programs.get(pl.get("program_id")) or {}).get(
                        "name"
                    ),
                    "instance": _instance_label(pl, venues),
                    "next_due": pending[0].get("due_date") if pending else None,
                    "section_count": len(secs),
                }
            )
        rows.sort(key=lambda r: str(r.get("person_name")))
        return {"plans": rows}

    if op == "plan":
        """One plan's schedulable sections: every section of the program, with
        any date/trainer already set."""
        plan_id = params.get("plan_id")
        plan = store.get("plans", plan_id)
        program = store.get("programs", plan.get("program_id"))
        modules = sorted(
            store.list("modules", where={"program_id": plan.get("program_id")}),
            key=lambda m: m.get("sort_index") or 0,
        )
        module_ids = {m["id"] for m in modules}
        sections = sorted(
            [s for s in store.list("sections") if s.get("module_id") in module_ids],
            key=lambda s: s.get("sort_index") or 0,
        )
        scheduled = {
            s.get("section_id"): s
            for s in store.list("plan_sections", where={"plan_id": plan_id})
        }
        rows = []
        for m in modules:
            for s in [x for x in sections if x.get("module_id") == m["id"]]:
                ps = scheduled.get(s["id"]) or {}
                rows.append(
                    {
                        "section_id": s["id"],
                        "module": m.get("name"),
                        "name": s.get("name") or "Section",
                        "section_type": s.get("section_type"),
                        "plan_section_id": ps.get("id"),
                        "due_date": ps.get("due_date"),
                        "start_time": ps.get("start_time"),
                        "status": ps.get("status") or "pending",
                    }
                )
        return {
            "plan": {
                "id": plan_id,
                "program": program.get("name"),
                "status": plan.get("status") or "active",
            },
            "sections": rows,
        }

    if op == "create_plan":
        person_id = params.get("person_id")
        program_id = params.get("program_id")
        if not person_id or not program_id:
            return {"error": "a person and a program are required"}
        return {
            "plan": store.put(
                "plans",
                {
                    "person_id": person_id,
                    "program_id": program_id,
                    "status": "active",
                    "variant_id": params.get("variant_id") or None,
                    "variant_name": params.get("variant_name"),
                    "created_at": _now(),
                },
                venue_id=params.get("venue_id") or None,
            )
        }

    if op == "schedule_section":
        """Give one plan section a date/time/trainer. Upsert on (plan, section)
        — Orbit's editor deleted and reinserted, which reset completion; this
        preserves any existing status."""
        plan_id = params.get("plan_id")
        section_id = params.get("section_id")
        existing = [
            x
            for x in store.list("plan_sections", where={"plan_id": plan_id})
            if x.get("section_id") == section_id
        ]
        data = {
            "plan_id": plan_id,
            "section_id": section_id,
            "due_date": params.get("due_date"),
            "start_time": params.get("start_time"),
            "trainer_person_id": params.get("trainer_person_id"),
            "status": existing[0].get("status") if existing else "pending",
        }
        if existing:
            data["status"] = existing[0].get("status") or "pending"
            return {"section": store.put("plan_sections", data, existing[0]["id"])}
        return {"section": store.put("plan_sections", data)}

    if op == "set_plan_section_status":
        section = store.get("plan_sections", params.get("plan_section_id"))
        data = {k: v for k, v in section.items() if k not in _META}
        status = params.get("status") or "pending"
        data["status"] = status
        data["completed_at"] = _now() if status == "completed" else None
        return {"section": store.put("plan_sections", data, section["id"])}

    if op == "set_plan_status":
        """Complete, reopen, or cancel a whole plan. The tracker trusts a plan's
        own status when it says completed (Orbit never wrote section status back,
        so most finished plans still have pending sections) — without this op a
        migrated 'active' plan could never be marked done from Norm."""
        plan_id = params.get("plan_id")
        status = params.get("status") or "active"
        if status not in ("active", "completed", "cancelled"):
            return {"error": f"'{status}' is not a plan status"}
        current = store.get("plans", plan_id)
        data = {k: v for k, v in current.items() if k not in _META}
        data["status"] = status
        return {"plan": store.put("plans", data, plan_id)}

    if op == "delete_plan":
        plan_id = params.get("plan_id")
        for x in store.list("plan_sections", where={"plan_id": plan_id}):
            store.delete("plan_sections", x["id"])
        store.delete("plans", plan_id)
        return {"ok": True}

    if op == "signoffs":
        """The manager sign-off queue: completions a trainee has submitted that
        need a manager to accept them. This is the query JSONB made reachable —
        awaiting_signoff true, no signoff_at yet — and it is the mechanism 396
        of the migrated completions already used."""
        pending = store.list(
            "completions",
            where={"awaiting_signoff": True, "signoff_at": {"is_null": True}},
        )
        content = _index(store.list("content"))
        assignments = _index(store.list("assignments"))
        people = _index(store.list("people"))
        programs = _index(store.list("programs"))
        rows = []
        for c in pending:
            a = assignments.get(c.get("assignment_id")) or {}
            item = content.get(c.get("content_id")) or {}
            person = people.get(a.get("person_id")) or {}
            program = programs.get(a.get("program_id")) or {}
            rows.append(
                {
                    "completion_id": c["id"],
                    "person": person.get("name") or "(unknown)",
                    "program": program.get("name"),
                    "item": item.get("title"),
                    "files": store.files("completions", c["id"]),
                }
            )
        rows.sort(key=lambda r: str(r.get("person")))
        return {"pending": rows}

    if op == "sign_off":
        """Accept a submitted item. Clears the awaiting flag and stamps who and
        when — the shape the tracker's effective-completion rule reads."""
        c = store.get("completions", params.get("completion_id"))
        data = {k: v for k, v in c.items() if k not in _META}
        data["awaiting_signoff"] = False
        data["signoff_at"] = _now()
        data["signoff_by"] = params.get("by") or "manager"
        if params.get("notes"):
            data["signoff_notes"] = params.get("notes")
        data.pop("rejected", None)
        return {"completion": store.put("completions", data, c["id"])}

    if op == "reject_signoff":
        """Send it back for another attempt, with a reason."""
        c = store.get("completions", params.get("completion_id"))
        data = {k: v for k, v in c.items() if k not in _META}
        data["rejected"] = True
        data["rejected_at"] = _now()
        data["rejection_notes"] = params.get("notes")
        return {"completion": store.put("completions", data, c["id"])}

    if op == "frameworks":
        """Capability frameworks — a role's competency map."""
        rows = store.list("capability_frameworks")
        for f in rows:
            f["category_count"] = len(f.get("categories") or [])
        rows.sort(key=lambda f: str(f.get("name")))
        return {"frameworks": rows}

    if op == "framework":
        return {
            "framework": store.get("capability_frameworks", params.get("framework_id"))
        }

    if op == "save_framework":
        fields = params.get("fields") or {}
        fid = params.get("framework_id")
        if fid:
            current = store.get("capability_frameworks", fid)
            data = {k: v for k, v in current.items() if k not in _META}
        else:
            data = {"categories": []}
        for key in ("name", "role_label", "is_active", "baseline_prerequisites"):
            if key in fields:
                data[key] = fields[key]
        # Full category/capability editing: the UI sends the whole tree. Give
        # every category and capability a stable id (migrated rows already carry
        # the Orbit id; new ones get one here) so performance-review ratings and
        # a later edit can point at them. No `uuid` in the sandbox — stamp from
        # the clock plus position, which is unique within a save.
        if "categories" in fields:
            cats = fields.get("categories") or []
            stamp = _now()
            for i, cat in enumerate(cats):
                if not cat.get("id"):
                    cat["id"] = "c-" + stamp + "-" + str(i)
                for j, cap in enumerate(cat.get("capabilities") or []):
                    if not cap.get("id"):
                        cap["id"] = "k-" + stamp + "-" + str(i) + "-" + str(j)
            data["categories"] = cats
        if not str(data.get("name") or "").strip():
            return {"error": "a framework needs a name"}
        return {"framework": store.put("capability_frameworks", data, fid)}

    return {"error": f"unknown op '{op}'"}
