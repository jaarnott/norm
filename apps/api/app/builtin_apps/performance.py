"""Performance — server-side logic.

Performance reviews, review cycles, the managers' daily digest, the goals
email and the capability frameworks reviews rate against. Moved out of Norm
Training (Oct 2026) unchanged: same ops, same rules, same data — the rows stay
in the shared ``hr_suite`` namespace, which Training owns and shares with this
app (``storage.shared_with``). People are Training's; this app reads them and
— only for "Review + employee profile" — sets a person's manager.

The UI is a sandboxed iframe that can only talk to this through
``norm.run({op: ...})``. The sandbox has no imports, so the small generic
helpers below are this app's own copies of Training's.
"""


def _today():
    # datetime.date.today() reaches for the time module via __import__, which
    # the sandbox blocks; datetime.now() (the pattern _now already uses) does not.
    return datetime.datetime.now(datetime.timezone.utc).date().isoformat()


def _now():
    return datetime.datetime.now(datetime.timezone.utc).isoformat()


def _index(rows, key="id"):
    return {r.get(key): r for r in rows}


def _group(rows, key):
    out = {}
    for r in rows:
        out.setdefault(r.get(key), []).append(r)
    return out


#: Storage adds these around a record; they must never be written back INTO
#: it, or an update would bury a copy of the envelope in the data.
_META = ("id", "venue_id", "created_at", "updated_at")


def _strip(record):
    return {k: v for k, v in record.items() if k not in _META}


def _copy(value):
    return json.loads(json.dumps(value))


def _esc(text):
    return (
        str("" if text is None else text)
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
    )


def _first(name):
    parts = str(name or "").strip().split()
    return parts[0] if parts else ""


def _active(person):
    # Rows made in Norm before `is_active` existed have no flag: active.
    return person.get("is_active", True) is not False


# ---- who is running this ---------------------------------------------------
#
# `_viewer` is set by the platform on every run (app_runtime.viewer_of) and a
# client cannot spoof it. The app maps it to its OWN people through
# `people.norm_user_id`, which the Orbit import links by email.

#: Who may administer performance reviews (see every review, change a
#: review's manager or framework, start/delete/restore reviews, run cycles,
#: read the email log) — and, like the review's effective manager, write the
#: manager's side. A platform admin, or an org role holding HR write or org
#: manage. The default Owner, Manager and Payroll Administrator roles qualify;
#: Team Member does not.
_REVIEW_ADMIN_PERMISSIONS = ("hr:write", "org:manage")


def _viewer(params):
    v = (params or {}).get("_viewer")
    return v if isinstance(v, dict) else {}


def _is_review_admin(viewer):
    if viewer.get("system") or viewer.get("is_admin"):
        return True
    held = viewer.get("permissions") or []
    return any(p in held for p in _REVIEW_ADMIN_PERMISSIONS)


def _me(viewer, people):
    """The viewer's own person record (active first), or None."""
    uid = viewer.get("user_id")
    if not uid:
        return None
    mine = [p for p in people if p.get("norm_user_id") == uid]
    mine.sort(key=lambda p: not _active(p))
    return mine[0] if mine else None


def _actor_fields(prefix, viewer, me):
    """`<prefix>` = the viewer's person id (when linked) and `<prefix>_name` =
    who they are — replacing the old hard-coded actor the UI used to send."""
    out = {prefix + "_name": viewer.get("name") or "Norm"}
    if me:
        out[prefix] = me["id"]
    return out


# ---- performance reviews ---------------------------------------------------
#
# Ported from Orbit (performanceReviews.ts, PerformanceReviewDetail.tsx, the
# scheduler and the emails function) with one deliberate difference: Orbit
# decided who may see and change what by the URL alone — its database let any
# manager edit any review and a team member write every column of their own,
# pay included. Here the FIELD RULES are enforced in this logic:
#
# * the manager's side (answers, capability levels, goal outcomes, next goals),
#   the two private questions and pay can be written only by the review's
#   EFFECTIVE manager (review.manager_id, else the person's manager_id) or a
#   review admin — never by the person being reviewed;
# * the private questions and pay are only ever returned to those two;
# * each side's answers stay hidden from the other until both are submitted;
# * the team member's side is read-only in Norm for now (staff logins are a
#   later project) — it arrives from the Orbit refresh.

_LEVEL_LABELS = {1: "Developing", 2: "Established", 3: "Expert"}
_OUTCOMES = ("achieved", "partial", "not")
_PRIVATE_FEEDBACK = ("job_offer_reaction", "engagement")
_PAY_FIELDS = (
    "current_pay_rate",
    "current_pay_type",
    "proposed_pay_rate",
    "no_pay_change",
    "pay_notes",
    "pay_proposed_by",
    "pay_proposed_by_orbit_id",
    "pay_proposed_by_name",
    "pay_proposed_at",
    # Orbit's generated review PDF holds the whole review, pay included.
    "pdf_storage_path",
)
_GOALS_SUBJECT = "Your performance review — goals for the next quarter"
_MONTHS = (
    "January",
    "February",
    "March",
    "April",
    "May",
    "June",
    "July",
    "August",
    "September",
    "October",
    "November",
    "December",
)


def _fmt_date(iso):
    y, m, d = (int(x) for x in str(iso)[:10].split("-"))
    return str(d) + " " + _MONTHS[m - 1] + " " + str(y)


def _date(iso):
    y, m, d = (int(x) for x in str(iso)[:10].split("-"))
    return datetime.date(y, m, d)


def _days_between(a, b):
    return (_date(b) - _date(a)).days


def _feedback(review):
    """The manager's answers, with Orbit's legacy key names folded in."""
    fb = dict(review.get("manager_feedback") or {})
    if not fb.get("does_well") and fb.get("strengths"):
        fb["does_well"] = fb.get("strengths")
    if not fb.get("improve") and fb.get("improvements"):
        fb["improve"] = fb.get("improvements")
    return fb


def _reflection(review):
    er = dict(review.get("employee_reflection") or {})
    if not er.get("does_well") and er.get("proud"):
        er["does_well"] = er.get("proud")
    return er


def _submission_state(review):
    """Orbit's reviewSubmissionState: the submission timestamps decide; the
    status is only a fallback for rows from before they existed."""
    status = review.get("status")
    has_ts = bool(
        review.get("employee_submitted_at") or review.get("manager_submitted_at")
    )
    meeting = status in ("completed", "ready_for_meeting", "in_meeting")
    employee = bool(review.get("employee_submitted_at")) or (
        not has_ts and (status == "manager_in_progress" or meeting)
    )
    manager = bool(review.get("manager_submitted_at")) or (not has_ts and meeting)
    return {
        "employee": employee,
        "manager": manager,
        "both": employee and manager,
        "completed": status == "completed",
    }


def _admin_label(review):
    st = _submission_state(review)
    if st["completed"]:
        return "Completed"
    if st["both"]:
        return "Ready to meet"
    if st["employee"]:
        return "Manager to complete"
    if st["manager"]:
        return "Team member to complete"
    return "Both to complete"


def _manager_label(review, first_name):
    st = _submission_state(review)
    if st["completed"]:
        return "Completed"
    if st["both"]:
        return "Ready to meet and finish"
    if not st["manager"]:
        return "Your part to complete" if st["employee"] else "Both to complete"
    return "Waiting on " + (first_name or "your team member")


def _effective_manager_id(review, people_idx):
    """The review's own manager, else the person's. `manager_cleared` is an
    explicit "No manager" for this review — Orbit's "No manager" cleared only
    the review's field, so the profile manager quietly applied anyway."""
    if review.get("manager_cleared"):
        return None
    if review.get("manager_id"):
        return review["manager_id"]
    return (people_idx.get(review.get("person_id")) or {}).get("manager_id")


def _app_settings():
    """This app's own settings row (one per org) — Norm-side only; the Orbit
    mirror never writes it."""
    rows = store.list("app_settings", limit=1)
    return rows[0] if rows else None


def _norm_runs_cycles():
    """Does Norm create cycle reviews each night? Off until cut-over: while
    Orbit is the live system it creates each round's reviews itself, and Norm
    doing it too would make a second set (the mirror never removes reviews
    Norm created)."""
    row = _app_settings()
    return bool(row and row.get("norm_runs_cycles"))


def _is_reviewee(review, viewer, me, people_idx):
    """Is the viewer the person being reviewed? Checked against the REVIEWEE's
    own record, not just the one record `_me` picked: a second linked row, an
    unlinked row or a row in a venue the viewer can't reach must not turn
    someone into a "not the reviewee" who can see their own pay. Unknown
    (the reviewee's record isn't visible) counts as "might be"."""
    person_id = review.get("person_id")
    if me and me.get("id") == person_id:
        return True
    person = people_idx.get(person_id)
    if person is None:
        return None
    uid = viewer.get("user_id")
    if uid and person.get("norm_user_id") == uid:
        return True
    email = str(viewer.get("email") or "").strip().lower()
    return bool(email) and str(person.get("email") or "").strip().lower() == email


def _access(review, viewer, me, people_idx):
    who = _is_reviewee(review, viewer, me, people_idx)
    reviewee = who is True
    manager_id = _effective_manager_id(review, people_idx)
    manager = bool(me) and bool(manager_id) and me["id"] == manager_id and who is False
    admin = _is_review_admin(viewer)
    if who is None:
        # Can't tell whose review this is: private parts only for a platform
        # admin, never on a guess.
        admin = admin and bool(viewer.get("is_admin"))
    private = (admin or manager) and not reviewee
    return {
        "admin": admin,
        "manager": manager,
        "reviewee": reviewee,
        "private": private,
        # The manager's side, pay and next goals.
        "write": private,
        "any": admin or manager or reviewee,
    }


def _framework_caps(framework):
    out = []
    for cat in (framework or {}).get("categories") or []:
        for cap in cat.get("capabilities") or []:
            if cap.get("id"):
                out.append(cap)
    return out


def _ratings_map(review):
    return {
        r.get("capability_id"): r
        for r in review.get("capability_ratings") or []
        if r.get("capability_id")
    }


def _pay_complete(review):
    if review.get("no_pay_change"):
        return True
    rate = review.get("proposed_pay_rate")
    return isinstance(rate, (int, float)) and not isinstance(rate, bool) and rate >= 0


def _manager_missing(review, framework):
    """The steps the manager still has to finish — Orbit's "Still to finish"
    rules, enforced here so a submit can't skip them.

    One deliberate difference: a review with no capability framework (Orbit's
    scheduler creates those and warns) has nothing to rate, so its
    Capabilities step counts as done. In Orbit it could never be submitted."""
    fb = _feedback(review)
    missing = []
    caps = _framework_caps(framework)
    ratings = _ratings_map(review)
    if caps and not (
        all((ratings.get(c["id"]) or {}).get("manager_level") for c in caps)
        and fb.get("capability_overall_level")
    ):
        missing.append("Capabilities")
    if not (
        fb.get("job_offer_reaction")
        and fb.get("engagement")
        and str(fb.get("does_well") or "").strip()
        and str(fb.get("improve") or "").strip()
        and fb.get("mgr_rating") is not None
    ):
        missing.append("Assessment")
    if not all(g.get("manager_outcome") for g in review.get("previous_goals") or []):
        missing.append("Goals")
    if not _pay_complete(review):
        missing.append("Pay")
    return missing


def _redacted(review, access):
    """What this viewer may see of a review."""
    st = _submission_state(review)
    shared = st["both"] or st["completed"]
    v = _copy(review)
    v["manager_feedback"] = _feedback(review)
    v["employee_reflection"] = _reflection(review)
    if not access["private"]:
        for key in _PRIVATE_FEEDBACK:
            v["manager_feedback"].pop(key, None)
        for key in _PAY_FIELDS:
            v.pop(key, None)
    show_employee = (
        access["reviewee"] or (access["private"] and access["admin"]) or shared
    )
    show_manager = access["private"] or shared
    if not show_employee:
        v["employee_reflection"] = {}
        for r in v.get("capability_ratings") or []:
            r["employee_level"] = None
            r.pop("employee_comment", None)
        for g in v.get("previous_goals") or []:
            g["employee_outcome"] = None
            g.pop("employee_comment", None)
    if not show_manager:
        v["manager_feedback"] = {}
        v["next_goals"] = []
        v.pop("summary_email_html", None)
        for r in v.get("capability_ratings") or []:
            r["manager_level"] = None
            r.pop("manager_comment", None)
        for g in v.get("previous_goals") or []:
            g["manager_outcome"] = None
            g.pop("manager_comment", None)
    v["employee_visible"] = bool(show_employee)
    v["manager_visible"] = bool(show_manager)
    return v


def _role_label(person):
    if person.get("loadedhub_role"):
        return person["loadedhub_role"]
    return {"head_office": "Head office", "manager": "Manager"}.get(
        person.get("role"), "Team member"
    )


def _venue_names(person, venues):
    return [venues[v] for v in person.get("venues") or [] if venues.get(v)]


def _current_rate(person_id):
    """The person's current pay as Orbit showed it (the import keeps one row
    per person — Orbit's currentRateForMember rule)."""
    if not person_id:
        return None
    rows = store.list("remuneration", where={"person_id": person_id}, limit=1)
    if not rows:
        return None
    row = rows[0]
    return {
        "pay_type": row.get("pay_type") or "hourly",
        "rate": row.get("rate"),
        "synced_at": row.get("synced_at"),
    }


def _put_review(review):
    return store.put("performance_reviews", _strip(review), review["id"])


def _carry_goals(person_id, reviews):
    """The next goals from the person's latest completed, non-deleted review
    become this one's previous goals. (Orbit's manual "Start Review" didn't
    skip deleted reviews; its scheduler did. Both skip them here.)"""
    done = [
        r
        for r in reviews
        if r.get("person_id") == person_id
        and r.get("status") == "completed"
        and not r.get("deleted_at")
    ]
    done.sort(key=lambda r: str(r.get("completed_at") or ""), reverse=True)
    if not done:
        return []
    stamp = _now()
    out = []
    for i, g in enumerate(done[0].get("next_goals") or []):
        text = g.get("text") if isinstance(g, dict) else g
        if str(text or "").strip():
            out.append(
                {"id": "g-" + stamp + "-" + str(i), "text": str(text), "sort_index": i}
            )
    return out


def _new_review(person_id, framework_id, due_date, cycle_id, reviews):
    return {
        "person_id": person_id,
        "cycle_id": cycle_id,
        "framework_id": framework_id,
        "due_date": due_date,
        "status": "employee_in_progress",
        "employee_reflection": {},
        "manager_feedback": {},
        "next_goals": [],
        "capability_ratings": [],
        "previous_goals": _carry_goals(person_id, reviews),
        "no_pay_change": False,
    }


def _create_review(data, person):
    """File a review made from the UI under the reviewee's venue, so it stays
    visible to the people who manage them; group-wide (venue_id="") when the
    person has no venue — never under whichever venue the picker happens to
    show. (The nightly run's reviews are group-wide.)"""
    for venue_id in (person.get("venues") or [])[:1]:
        try:
            return store.put("performance_reviews", data, venue_id=venue_id)
        except Exception:
            pass
    return store.put("performance_reviews", data, venue_id="")


def _untouched(r):
    """A review nobody has started — safe to withdraw when its person leaves
    the cycle. Orbit's rule, plus: no manager submission and no pay."""
    if r.get("status") != "employee_in_progress":
        return False
    if r.get("employee_submitted_at") or r.get("manager_submitted_at"):
        return False
    for x in r.get("capability_ratings") or []:
        if (
            x.get("employee_level")
            or x.get("manager_level")
            or x.get("employee_comment")
            or x.get("manager_comment")
        ):
            return False
    for g in r.get("previous_goals") or []:
        if (
            g.get("employee_outcome")
            or g.get("manager_outcome")
            or g.get("employee_comment")
            or g.get("manager_comment")
        ):
            return False
    for key in ("employee_reflection", "manager_feedback"):
        obj = r.get(key) or {}
        if isinstance(obj, dict) and any(
            v is not None and str(v).strip() != "" for v in obj.values()
        ):
            return False
    if r.get("next_goals"):
        return False
    if r.get("proposed_pay_rate") is not None or r.get("pay_notes"):
        return False
    return True


# ---- auto-enrol filter (Orbit's member_matches_filter) ----------------------


def _cond_match(person, cond):
    attr, op, val = cond.get("attr"), cond.get("op"), cond.get("value")
    many = [str(x) for x in val] if isinstance(val, list) else []
    one = "" if val is None or isinstance(val, list) else str(val)

    def scalar(have):
        have = str(have or "")
        if op == "is":
            return have == one
        if op == "is_not":
            return have != one
        if op == "any_of":
            return have in many
        if op == "none_of":
            return have not in many
        return False

    def multi(have):
        have = {str(h) for h in have if h}
        if op == "is":
            return one in have
        if op == "is_not":
            return one not in have
        if op == "any_of":
            return bool(have & set(many))
        if op == "none_of":
            return not (have & set(many))
        return False

    if attr == "role":
        return scalar(person.get("role"))
    if attr == "loadedhub_role":
        return scalar(person.get("loadedhub_role"))
    if attr == "payroll_company":
        return scalar(person.get("payroll_company_id"))
    if attr == "venue":
        return multi(person.get("venues") or [])
    if attr == "team_member":
        # A filter made in Orbit names Orbit's team-member ids.
        return multi([person.get("id"), person.get("orbit_id")])
    if attr == "name":
        name = str(person.get("name") or "").lower()
        if op == "contains":
            return one.lower() in name
        if op == "is":
            return name == one.lower()
        if op == "is_not":
            return name != one.lower()
        return False
    if attr == "created_at":
        created = str(person.get("created_at") or "")
        if op == "before":
            return bool(created) and created < one
        if op == "after":
            return bool(created) and created > one
        if op == "between" and len(many) == 2:
            return bool(created) and many[0] <= created <= many[1]
        return False
    if attr == "has_email":
        has = bool(str(person.get("email") or "").strip())
        return has if one.lower() in ("true", "yes", "1") else not has
    return False


def _filter_match(person, f):
    """Include groups OR'd (each group's conditions AND'd; an empty group
    matches everyone), then any non-empty matching exclude group excludes."""
    if not isinstance(f, dict):
        return False
    include = f.get("include") or []
    if not include:
        return False
    if not any(
        all(_cond_match(person, c) for c in (g or {}).get("conditions") or [])
        for g in include
    ):
        return False
    for g in f.get("exclude") or []:
        conds = (g or {}).get("conditions") or []
        if conds and all(_cond_match(person, c) for c in conds):
            return False
    return True


# ---- review cycles (Orbit's performance-reviews-scheduler) ------------------


def _parse_cadence(cadence):
    cadence = str(cadence or "")
    if cadence.startswith("custom:"):
        parts = cadence.split(":")
        if len(parts) != 3:
            return None
        try:
            n = int(parts[1])
        except Exception:
            return None
        if n < 1 or parts[2] not in ("days", "weeks", "months", "years"):
            return None
        return (n, parts[2])
    return {
        "quarterly": (3, "months"),
        "six_monthly": (6, "months"),
        "annual": (12, "months"),
    }.get(cadence)


def _add_months(d, n):
    total = d.month - 1 + n
    y, m = d.year + total // 12, total % 12 + 1
    nxt = datetime.date(y + 1, 1, 1) if m == 12 else datetime.date(y, m + 1, 1)
    last = (nxt - datetime.timedelta(days=1)).day
    return datetime.date(y, m, min(d.day, last))


def _add_cadence(iso, cadence, times=1):
    """``times`` steps on from ``iso``; a month step keeps the day, clamped to
    the month's end."""
    n, unit = cadence
    n = n * times
    d = _date(iso)
    if unit == "days":
        d = d + datetime.timedelta(days=n)
    elif unit == "weeks":
        d = d + datetime.timedelta(days=7 * n)
    elif unit == "months":
        d = _add_months(d, n)
    else:
        d = _add_months(d, 12 * n)
    return d.isoformat()


def _next_due(dates, cadence):
    """The next date after the last one, counted from the FIRST date. Orbit
    stepped from the last date, so a cycle on the 31st slid to the 28th after
    February and stayed there."""
    anchor, last = dates[0], dates[-1]
    k = 1
    while k < 1000:
        nxt = _add_cadence(anchor, cadence, k)
        if nxt > last:
            return nxt
        k += 1
    return _add_cadence(last, cadence)


def _cycle_members(cycle, people, people_idx):
    """person id -> the framework their manual assignment names (or None).
    Manual assignments plus auto-enrol matches; former staff never qualify
    (Orbit kept enrolling a manually-assigned person after they left)."""
    members = {}
    for a in cycle.get("assignments") or []:
        p = people_idx.get(a.get("person_id"))
        if p and _active(p):
            members[p["id"]] = a.get("framework_id")
    f = cycle.get("auto_enroll_filter")
    if isinstance(f, dict) and f.get("include"):
        for p in people:
            if _active(p) and p["id"] not in members and _filter_match(p, f):
                members[p["id"]] = None
    return members


def _cycle_plan(cycle, ctx, today, dry_run):
    """What enrolment does for one cycle, and — unless ``dry_run`` — does it:
    roll the dates forward, create the round's reviews once its window is
    open (carrying goals forward), and withdraw untouched reviews for people
    who no longer belong. ``ctx["reviews"]`` is kept current as it goes."""
    people_idx = ctx["people_idx"]
    reviews = ctx["reviews"]
    lead = cycle.get("lead_days")
    lead = 28 if lead in (None, "") else int(lead)
    result = {
        "cycle_id": cycle["id"],
        "cycle_name": cycle.get("name"),
        "due_date": None,
        "window_open": False,
        "created": [],
        "removed": [],
        "deletion_blocked": [],
        "missing_framework": [],
        "already_enrolled": 0,
        "matched_count": 0,
        "added_due_dates": [],
        "missed_rounds": [],
    }

    def entry(person_id, review_id=None):
        e = {
            "person_id": person_id,
            "name": (people_idx.get(person_id) or {}).get("name") or "Unknown",
        }
        if review_id:
            e["review_id"] = review_id
        return e

    original = sorted({str(d)[:10] for d in cycle.get("due_dates") or [] if d})
    dates = list(original)
    if not dates:
        result["error"] = "no review dates set"
        return result
    cadence = _parse_cadence(cycle.get("cadence"))
    if cadence:
        guard = 0
        while not any(d >= today for d in dates) and guard < 60:
            dates.append(_next_due(dates, cadence))
            guard += 1
        upcoming = [d for d in dates if d >= today]
        guard = 0
        while len(upcoming) < 2 and guard < 5:
            nxt = _next_due(dates, cadence)
            dates.append(nxt)
            upcoming.append(nxt)
            guard += 1
        dates = sorted(set(dates))
        result["added_due_dates"] = [d for d in dates if d not in original]
        if result["added_due_dates"] and not dry_run:
            data = _strip(cycle)
            data["due_dates"] = dates
            store.put("review_cycles", data, cycle["id"])
    result["due_dates"] = dates

    covered = {
        str(r.get("due_date") or "")[:10]
        for r in reviews
        if r.get("cycle_id") == cycle["id"]
    }
    result["missed_rounds"] = [d for d in dates if d < today and d not in covered]
    upcoming = [d for d in dates if d >= today]
    if not upcoming:
        result["error"] = "no upcoming review date"
        return result
    due = upcoming[0]
    result["due_date"] = due
    opens = (_date(due) - datetime.timedelta(days=lead)).isoformat()
    result["enrolment_opens"] = opens
    result["window_open"] = opens <= today

    members = _cycle_members(cycle, ctx["people"], people_idx)
    result["matched_count"] = len(members)
    existing = [
        r
        for r in reviews
        if r.get("cycle_id") == cycle["id"] and str(r.get("due_date") or "")[:10] == due
    ]
    by_person = _group(existing, "person_id")

    if result["window_open"]:
        order = sorted(
            members, key=lambda pid: str((people_idx.get(pid) or {}).get("name") or "")
        )
        for pid in order:
            rows = by_person.get(pid, [])
            if any(not r.get("deleted_at") for r in rows):
                result["already_enrolled"] += 1
                continue
            if rows:
                # Deliberately deleted for this round: respected, and said.
                result["deletion_blocked"].append(entry(pid))
                continue
            framework_id = members[pid] or cycle.get("default_framework_id")
            if not framework_id:
                result["missing_framework"].append(entry(pid))
            if dry_run:
                result["created"].append(entry(pid))
                continue
            # Group-wide (venue_id=""): visible to the reviewee's managers
            # whatever venue is selected, and a venue's deletion can't take it.
            made = store.put(
                "performance_reviews",
                _new_review(pid, framework_id, due, cycle["id"], reviews),
                venue_id="",
            )
            reviews.append(made)
            result["created"].append(entry(pid, made["id"]))

    # Withdraw untouched reviews for people who no longer belong — even while
    # the window is closed, as Orbit does.
    for r in existing:
        if r.get("deleted_at") or r.get("person_id") in members or not _untouched(r):
            continue
        result["removed"].append(entry(r.get("person_id"), r["id"]))
        if not dry_run:
            r["deleted_at"] = _now()
            r["deleted_by"] = None
            r["withdrawn"] = True
            _put_review(r)
    return result


def _reviews_ctx(params):
    people = store.list("people")
    viewer = _viewer(params)
    return {
        "people": people,
        "people_idx": _index(people),
        "viewer": viewer,
        "me": _me(viewer, people),
        "reviews": store.list("performance_reviews"),
        "venues": {
            v.get("id"): v.get("name") for v in (params or {}).get("venues") or []
        },
    }


# ---- the manager daily digest (Orbit's send-performance-review-emails) -------


def _interval_for(days_left):
    """Reminder cadence tightens as the due date approaches."""
    if days_left < 0:
        return 2
    if days_left <= 3:
        return 1
    if days_left <= 14:
        return 3
    return 7


def _digest_plan(ctx, today, test=False):
    """One digest per effective manager who needs one today.

    Items are reviews the manager hasn't submitted (Overdue / Ready for you
    to complete) and reviews both have submitted (Ready for your one-on-one);
    reviews waiting only on the team member are left out. A digest goes when
    an item has never been in one, a team member submitted since the last
    one, or a non-meeting item's reminder interval has passed — at most one a
    day. History is the app's own `performance_review_emails` (Orbit's rows
    arrive with the refresh, so the two systems' digests count together).
    """
    pidx = ctx["people_idx"]
    open_reviews = [
        r
        for r in ctx["reviews"]
        if not r.get("deleted_at")
        and r.get("status") != "completed"
        and r.get("due_date")
    ]
    by_manager = {}
    for r in open_reviews:
        m = _effective_manager_id(r, pidx)
        if m and m != r.get("person_id"):
            by_manager.setdefault(m, []).append(r)
    history = [
        e
        for e in store.list(
            "performance_review_emails", where={"kind": "manager_digest"}
        )
        if e.get("status") == "sent" and not e.get("is_test")
    ]
    history.sort(key=lambda e: str(e.get("sent_at") or ""), reverse=True)
    out = []
    for manager_id in sorted(
        by_manager, key=lambda i: str((pidx.get(i) or {}).get("name") or "")
    ):
        manager = pidx.get(manager_id)
        if not manager or not _active(manager):
            continue
        items = []
        for r in by_manager[manager_id]:
            person = pidx.get(r.get("person_id")) or {}
            if not _active(person):
                continue  # they've left; nobody needs nagging about it
            st = _submission_state(r)
            if st["manager"] and not st["both"]:
                continue
            days_left = _days_between(today, r["due_date"])
            items.append(
                {
                    "id": r["id"],
                    "name": person.get("name") or "Team member",
                    "role": person.get("loadedhub_role") or "",
                    "venue": ", ".join(_venue_names(person, ctx["venues"])),
                    "due": str(r["due_date"])[:10],
                    "days_left": days_left,
                    "employee_submitted": st["employee"],
                    "employee_submitted_at": r.get("employee_submitted_at"),
                    "bucket": "meeting"
                    if st["both"]
                    else ("overdue" if days_left < 0 else "todo"),
                }
            )
        if not items:
            continue
        mine = [e for e in history if e.get("person_id") == manager_id]
        last_at = mine[0].get("sent_at") if mine else None
        ever = set()
        for e in mine:
            for rid in e.get("review_ids") or []:
                ever.add(rid)
        trigger = test
        for i in items:
            if i["id"] not in ever:
                trigger = True
            elif (
                last_at
                and i["employee_submitted_at"]
                and str(i["employee_submitted_at"]) > str(last_at)
            ):
                trigger = True
            elif (
                i["bucket"] != "meeting"
                and last_at
                and _days_between(str(last_at)[:10], today)
                >= _interval_for(i["days_left"])
            ):
                trigger = True
            if trigger:
                break
        if not trigger:
            continue
        if last_at and str(last_at)[:10] == today and not test:
            continue
        out.append({"manager": manager, "items": items})
    return out


def _digest_email(manager, items):
    def link(i):
        return email.link({"view": "my_team", "review": i["id"]})

    def line(i):
        a = (
            '<a href="'
            + _esc(link(i))
            + '" style="color:#49556A;font-weight:600;">'
            + _esc(i["name"])
            + "</a>"
        )
        if i["bucket"] == "meeting":
            return (
                '<li style="margin-bottom:6px;">'
                + a
                + ": you've both submitted. Open it together to go through it and "
                "agree the goals.</li>"
            )
        meta = ", ".join(_esc(x) for x in (i["role"], i["venue"]) if x)
        first = _esc(_first(i["name"]) or "They")
        sub = (
            first + " has submitted and is waiting on you."
            if i["employee_submitted"]
            else first + " hasn't submitted yet."
        )
        due = "due " + _fmt_date(i["due"])
        if i["bucket"] == "overdue":
            n = -i["days_left"]
            due += ", " + str(n) + " day" + ("" if n == 1 else "s") + " overdue"
        return (
            '<li style="margin-bottom:6px;">'
            + a
            + (", " + meta if meta else "")
            + ": "
            + due
            + ". "
            + sub
            + "</li>"
        )

    def section(title, rows):
        if not rows:
            return ""
        return (
            '<p style="margin:16px 0 6px 0;font-weight:700;">'
            + title
            + '</p><ul style="margin:0 0 12px 0;padding-left:20px;">'
            + "".join(line(i) for i in rows)
            + "</ul>"
        )

    overdue = [i for i in items if i["bucket"] == "overdue"]
    todo = [i for i in items if i["bucket"] == "todo"]
    meeting = [i for i in items if i["bucket"] == "meeting"]
    n = len(overdue) + len(todo)
    if n:
        subject = (
            "You have "
            + str(n)
            + " performance review"
            + ("" if n == 1 else "s")
            + " to complete"
        )
    else:
        m = len(meeting)
        subject = (
            "You have "
            + str(m)
            + " performance review"
            + ("" if m == 1 else "s")
            + " ready for a one-on-one"
        )
    p = '<p style="margin:0 0 12px 0;">'
    html = (
        p
        + "Hi "
        + _esc(_first(manager.get("name")) or "there")
        + ",</p>"
        + p
        + "Here's where your team's performance reviews are up to.</p>"
        + section("Overdue", overdue)
        + section("Ready for you to complete", todo)
        + section("Ready for your one-on-one", meeting)
        + '<p style="margin:0 0 6px 0;">To complete a review:</p>'
        + '<ol style="margin:0 0 12px 0;padding-left:20px;">'
        + "<li>Open it from the link above (or Performance → My team in Norm)</li>"
        + "<li>Complete Capabilities, Assessment, Goals and Pay</li>"
        + "<li>Submit once all sections are complete</li></ol>"
        + p
        + "Please rate each person on the level they meet consistently, not on "
        "their best shift.</p>"
    )
    return subject, html


def _send_digests(ctx, today, slot, mode, viewer):
    """Send (or, in dry_run, render) today's digests. ``mode`` is the run's
    email mode: live from the schedule; dry_run / test from the log page."""
    test = mode == "test"
    plans = _digest_plan(ctx, today, test=test)
    summary = {"planned": len(plans), "statuses": {}, "emails": []}
    button = {
        "label": "Open Team Reviews",
        "url": email.link({"view": "my_team"}),
    }
    for d in plans:
        manager, items = d["manager"], d["items"]
        subject, html = _digest_email(manager, items)
        to = ["person:" + manager["id"]]
        if test:
            # A test goes to the person running it — whose address belongs to
            # the org — whatever the manager's own address is.
            to = [viewer.get("email")] if viewer.get("email") else to
        res = email.send(
            to,
            subject,
            html,
            key=None if test else "digest:" + manager["id"] + ":" + slot,
            kind="manager_digest",
            heading="Your Team Reviews",
            button=button,
        )
        status = res.get("status") or "failed"
        summary["statuses"][status] = summary["statuses"].get(status, 0) + 1
        row = {
            "manager": manager.get("name"),
            "to": res.get("to") or [],
            "subject": subject,
            "items": len(items),
            "status": status,
        }
        if res.get("reason"):
            row["reason"] = res.get("reason")
        if mode == "dry_run" and len(summary["emails"]) < 25:
            row["html"] = res.get("html")
        summary["emails"].append(row)
        if status == "sent" and mode == "live":
            store.put(
                "performance_review_emails",
                {
                    "person_id": manager["id"],
                    "recipient_role": "manager",
                    "kind": "manager_digest",
                    "review_ids": [i["id"] for i in items],
                    "to_email": ", ".join(res.get("to") or []),
                    "subject": subject,
                    "status": "sent",
                    "is_test": False,
                    "sent_at": _now(),
                    "source": "norm",
                },
            )
        if test:
            break  # one sample is enough
    return summary


def _goals_email_html(intro, goals, sign_off):
    """Orbit's buildGoalsEmailHtml: paragraphs, the goals as a list, sign-off."""
    paras = [x.strip() for x in str(intro or "").replace("\r\n", "\n").split("\n\n")]
    body = "".join(
        '<p style="margin:0 0 12px 0;">' + _esc(x).replace("\n", "<br/>") + "</p>"
        for x in paras
        if x
    )
    if goals:
        body += (
            '<ul style="margin:0 0 12px 0;padding-left:20px;">'
            + "".join(
                '<li style="margin-bottom:6px;">' + _esc(g) + "</li>" for g in goals
            )
            + "</ul>"
        )
    if str(sign_off or "").strip():
        body += (
            '<p style="margin:0;">'
            + _esc(str(sign_off).strip()).replace("\n", "<br/>")
            + "</p>"
        )
    return (
        '<div style="font-family:Arial,Helvetica,sans-serif;font-size:14px;'
        'color:#222;line-height:1.5;">' + body + "</div>"
    )


def _apply_pay(review, pay, viewer, me):
    """Validate and record a pay decision (with the current rate captured
    beside it). Returns an error message, or None."""
    no_change = bool(pay.get("no_pay_change"))
    raw = pay.get("proposed_pay_rate")
    rate = None
    if not no_change and raw not in (None, ""):
        if isinstance(raw, bool):
            return "Enter a valid pay rate"
        try:
            rate = float(raw)
        except Exception:
            return "Enter a valid pay rate"
        if rate != rate or rate < 0 or rate > 10000000:
            return "Enter a valid pay rate"
    current = _current_rate(review.get("person_id"))
    review["proposed_pay_rate"] = rate
    review["no_pay_change"] = no_change
    review["pay_notes"] = str(pay.get("pay_notes") or "").strip() or None
    review["current_pay_rate"] = current.get("rate") if current else None
    review["current_pay_type"] = current.get("pay_type") if current else None
    review["pay_proposed_at"] = _now()
    review.pop("pay_proposed_by", None)
    review.update(_actor_fields("pay_proposed_by", viewer, me))
    return None


def _clean_goals(raw):
    out = []
    for g in raw or []:
        text = g.get("text") if isinstance(g, dict) else g
        text = str(text or "").strip()
        if text:
            out.append(text[:500])
    return out


def _apply_feedback(review, fields):
    """The manager's own answers — only the keys the form has, text capped."""
    fb = _feedback(review)
    for key in ("does_well", "improve", "job_offer_reaction", "engagement"):
        if key in fields:
            fb[key] = str(fields.get(key) or "")[:5000]
    if "mgr_rating" in fields:
        v = fields.get("mgr_rating")
        fb["mgr_rating"] = v if v in (0, 1, 2, 3, 4) else None
    if "capability_overall_level" in fields:
        v = fields.get("capability_overall_level")
        fb["capability_overall_level"] = v if v in (1, 2, 3) else None
    review["manager_feedback"] = fb


def _review_row(r, pidx, venues, frameworks):
    person = pidx.get(r.get("person_id")) or {}
    manager_id = _effective_manager_id(r, pidx)
    deleted_by = pidx.get(r.get("deleted_by")) or {}
    return {
        "id": r["id"],
        "person_id": r.get("person_id"),
        "person_name": person.get("name") or "(unknown)",
        "person_venues": person.get("venues") or [],
        "role_label": _role_label(person),
        "venue_names": _venue_names(person, venues),
        "manager_id": manager_id,
        "manager_name": (pidx.get(manager_id) or {}).get("name")
        if manager_id
        else None,
        "framework_id": r.get("framework_id"),
        "framework_name": (frameworks.get(r.get("framework_id")) or {}).get("name"),
        "due_date": r.get("due_date"),
        "status": r.get("status"),
        "label": _admin_label(r),
        "manager_label": _manager_label(r, _first(person.get("name"))),
        "completed_at": r.get("completed_at"),
        "deleted_at": r.get("deleted_at"),
        "deleted_by_name": deleted_by.get("name") or r.get("deleted_by_name"),
        "withdrawn": bool(r.get("withdrawn")),
    }


def _manager_options(people):
    rows = [
        {"id": p["id"], "name": p.get("name"), "role": p.get("role")}
        for p in people
        if _active(p) and p.get("role") in ("manager", "head_office")
    ]
    rows.sort(key=lambda p: str(p.get("name") or ""))
    return rows


def _one_review(params):
    """One review plus who is looking at it — reading only the people it
    needs (the viewer's own record, the reviewee, their managers and whoever
    completed or deleted it), not the whole team: each rating click is a run."""
    review = store.get("performance_reviews", (params or {}).get("review_id"))
    viewer = _viewer(params)
    uid = viewer.get("user_id")
    mine = store.list("people", where={"norm_user_id": uid}) if uid else []
    pidx = _index(mine)

    def fetch(person_id):
        if person_id and person_id not in pidx:
            try:
                pidx[person_id] = store.get("people", person_id)
            except Exception:
                pass

    fetch(review.get("person_id"))
    fetch(review.get("manager_id"))
    fetch((pidx.get(review.get("person_id")) or {}).get("manager_id"))
    for key in ("completed_by", "deleted_by"):
        fetch(review.get(key))
    me = _me(viewer, mine)
    return review, {
        "people_idx": pidx,
        "viewer": viewer,
        "me": me,
        "access": _access(review, viewer, me, pidx),
        "venues": {
            v.get("id"): v.get("name") for v in (params or {}).get("venues") or []
        },
    }


def _framework_for(review):
    if not review.get("framework_id"):
        return None
    try:
        return store.get("capability_frameworks", review["framework_id"])
    except Exception:
        return None


def _review_payload(review, ctx):
    access = ctx["access"]
    pidx = ctx["people_idx"]
    person = pidx.get(review.get("person_id")) or {}
    manager_id = _effective_manager_id(review, pidx)
    manager = (pidx.get(manager_id) or {}) if manager_id else {}
    framework = _framework_for(review)
    st = _submission_state(review)
    first = _first(person.get("name"))
    out = {
        "review": _redacted(review, access),
        "person": {
            "id": person.get("id"),
            "name": person.get("name") or "(unknown)",
            "first": first,
            "role_label": _role_label(person) if person else None,
            "venue_names": _venue_names(person, ctx["venues"]),
            "is_active": _active(person) if person else False,
            # The goals email's To — only for someone who may complete it.
            "email": person.get("email") if access["write"] else None,
        },
        "manager": {
            "id": manager_id,
            "name": manager.get("name"),
            "first": _first(manager.get("name")),
            "email": manager.get("email") if access["write"] else None,
            "has_login": bool(manager.get("norm_user_id")),
            "is_you": bool(ctx["me"]) and ctx["me"]["id"] == manager_id,
        },
        "review_manager_id": review.get("manager_id"),
        "manager_cleared": bool(review.get("manager_cleared")),
        "framework": {
            "id": framework["id"],
            "name": framework.get("name"),
            "categories": framework.get("categories") or [],
        }
        if framework
        else None,
        "current_rate": _current_rate(review.get("person_id"))
        if access["private"]
        else None,
        "access": access,
        "state": st,
        "labels": {
            "admin": _admin_label(review),
            "manager": _manager_label(review, first),
        },
        "missing": _manager_missing(review, framework) if access["write"] else [],
        "completed_by_name": (pidx.get(review.get("completed_by")) or {}).get("name")
        or review.get("completed_by_name"),
        "deleted_by_name": (pidx.get(review.get("deleted_by")) or {}).get("name")
        or review.get("deleted_by_name"),
    }
    if access["write"]:
        out["email_enabled"] = email.enabled()
    return out


def run(params, call_api, log):
    op = (params or {}).get("op") or "my_team_reviews"
    venues = {v.get("id"): v.get("name") for v in (params or {}).get("venues") or []}
    viewer = _viewer(params)
    me = None

    if op == "locate":
        """Where Norm asked the app to open (its component's inputs, see
        services/app_components.py). A review is opened by id (the digest
        and goals emails link to one); whether this viewer may see it is
        the `review` op's call."""
        out = {"review_id": None}
        if params.get("review"):
            try:
                out["review_id"] = store.get(
                    "performance_reviews", params.get("review")
                )["id"]
            except Exception:
                return {"error": "That performance review no longer exists."}
        return out

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

    # ---- Orbit refresh banner --------------------------------------------

    if op == "sync_status":
        """When the Orbit import last brought this app's data in: the newest
        row any refresh created (Orbit is live, so every refresh brings new
        rows). Rows Norm creates have no orbit_id and don't count."""
        latest = None
        for coll in (
            "people",
            "performance_reviews",
            "review_cycles",
            "capability_frameworks",
        ):
            rows = store.list(
                coll, where={"orbit_id": {"is_null": False}}, descending=True, limit=1
            )
            for r in rows:
                stamp = str(r.get("created_at") or "")
                if stamp and (latest is None or stamp > latest):
                    latest = stamp
        return {"refreshed_at": latest}

    # ---- Performance reviews: lists ---------------------------------------

    if op == "review_badge":
        """The Reviews tab's count: my team's reviews waiting on me — the
        manager hasn't submitted, or both have and it's ready to meet."""
        people = store.list("people")
        me = _me(viewer, people)
        todo = 0
        if me:
            pidx = _index(people)
            for r in store.list("performance_reviews"):
                if r.get("deleted_at") or r.get("status") == "completed":
                    continue
                if r.get("person_id") == me["id"]:
                    continue
                if _effective_manager_id(r, pidx) != me["id"]:
                    continue
                st = _submission_state(r)
                if not st["manager"] or st["both"]:
                    todo += 1
        return {
            "team_todo": todo,
            "linked": bool(me),
            "admin": _is_review_admin(viewer),
        }

    if op == "reviews":
        """Every review, for review admins (Orbit's Hiring → Performance
        Reviews)."""
        if not _is_review_admin(viewer):
            return {"error": "Only HR admins see every review — My team has yours."}
        people = store.list("people")
        pidx = _index(people)
        frameworks = _index(store.list("capability_frameworks"))
        rows = [
            _review_row(r, pidx, venues, frameworks)
            for r in store.list("performance_reviews")
            if params.get("include_deleted") or not r.get("deleted_at")
        ]
        rows.sort(key=lambda r: str(r.get("due_date") or ""), reverse=True)
        return {
            "reviews": rows,
            "managers": _manager_options(people),
            "frameworks": sorted(
                [
                    {
                        "id": f["id"],
                        "name": f.get("name"),
                        "is_active": f.get("is_active", True),
                    }
                    for f in frameworks.values()
                ],
                key=lambda f: str(f.get("name") or ""),
            ),
            "people": sorted(
                [{"id": p["id"], "name": p.get("name")} for p in people if _active(p)],
                key=lambda p: str(p.get("name") or ""),
            ),
        }

    if op == "my_team_reviews":
        """Reviews where I'm the effective manager: in progress (ready to meet
        first, then my part, then waiting; then by due date) and past."""
        people = store.list("people")
        pidx = _index(people)
        me = _me(viewer, people)
        if not me:
            return {"linked": False, "current": [], "past": []}
        frameworks = _index(store.list("capability_frameworks"))
        mine = [
            r
            for r in store.list("performance_reviews")
            if not r.get("deleted_at")
            and r.get("person_id") != me["id"]
            and _effective_manager_id(r, pidx) == me["id"]
        ]

        def priority(r):
            st = _submission_state(r)
            if st["both"]:
                return 0
            return 1 if not st["manager"] else 2

        current = [r for r in mine if r.get("status") != "completed"]
        current.sort(key=lambda r: (priority(r), str(r.get("due_date") or "")))
        past = [r for r in mine if r.get("status") == "completed"]
        past.sort(key=lambda r: str(r.get("completed_at") or ""), reverse=True)
        today = _today()
        out_current = []
        for r in current:
            row = _review_row(r, pidx, venues, frameworks)
            row["overdue"] = bool(r.get("due_date")) and str(r["due_date"])[:10] < today
            out_current.append(row)
        return {
            "linked": True,
            "me": {"id": me["id"], "name": me.get("name")},
            "current": out_current,
            "past": [_review_row(r, pidx, venues, frameworks) for r in past],
        }

    # ---- Performance reviews: one review ----------------------------------

    if op == "review":
        review, ctx = _one_review(params)
        if not ctx["access"]["any"]:
            return {"error": "You don't have access to this review."}
        return _review_payload(review, ctx)

    if op in (
        "review_save_manager",
        "review_rate",
        "review_goal_outcome",
        "review_save_pay",
        "review_submit_manager",
        "review_complete",
        "review_unlock",
    ):
        review, ctx = _one_review(params)
        access, me = ctx["access"], ctx["me"]
        if not access["write"]:
            if access["reviewee"]:
                return {
                    "error": "This is your own review — your manager completes this part."
                }
            return {
                "error": "Only this review's manager (or an HR admin) can change "
                "the manager's side."
            }
        if review.get("deleted_at"):
            return {"error": "This review has been deleted — restore it first."}
        st = _submission_state(review)

        if op == "review_unlock":
            """Reopen a completed review so the manager can resubmit. Orbit's
            unlock left the manager's submission in place, so it only ever
            reopened the complete step; this clears it, as the button says."""
            if not st["completed"]:
                return {"error": "Only a completed review can be unlocked."}
            if st["employee"] and not review.get("employee_submitted_at"):
                review["employee_submitted_at"] = review.get("completed_at") or _now()
            review["manager_submitted_at"] = None
            review["status"] = (
                "manager_in_progress" if st["employee"] else "employee_in_progress"
            )
            review["completed_at"] = None
            review["completed_by"] = None
            review.pop("completed_by_name", None)
            review["reopened_count"] = int(review.get("reopened_count") or 0) + 1
            _put_review(review)
            return {"ok": True}

        if st["completed"]:
            return {"error": "This review is completed — unlock it to make changes."}
        manager_open = not st["manager"]

        if op == "review_rate":
            if not manager_open:
                return {"error": "Your part is already submitted."}
            framework = _framework_for(review)
            cap_id = params.get("capability_id")
            if cap_id not in {c["id"] for c in _framework_caps(framework)}:
                return {"error": "That capability isn't on this review's framework."}
            level = params.get("level")
            if level not in (1, 2, 3, None):
                return {"error": "A level is 1, 2 or 3."}
            ratings = review.get("capability_ratings") or []
            hit = [r for r in ratings if r.get("capability_id") == cap_id]
            if hit:
                hit[0]["manager_level"] = level
            else:
                ratings.append(
                    {
                        "capability_id": cap_id,
                        "employee_level": None,
                        "manager_level": level,
                    }
                )
            review["capability_ratings"] = ratings
            _put_review(review)
            return {"ok": True}

        if op == "review_goal_outcome":
            if not manager_open:
                return {"error": "Your part is already submitted."}
            outcome = params.get("outcome")
            if outcome not in _OUTCOMES and outcome is not None:
                return {"error": "An outcome is achieved, partial or not."}
            goals = review.get("previous_goals") or []
            hit = [g for g in goals if g.get("id") == params.get("goal_id")]
            if not hit:
                return {"error": "That goal isn't on this review."}
            hit[0]["manager_outcome"] = outcome
            _put_review(review)
            return {"ok": True}

        if op == "review_save_manager":
            fields = params.get("fields") or {}
            if any(k != "next_goals" for k in fields):
                if not manager_open:
                    return {"error": "Your part is already submitted."}
                _apply_feedback(review, fields)
            if "next_goals" in fields:
                # Next-period goals are agreed in the meeting, by the manager.
                if not st["both"]:
                    return {
                        "error": "Goals are set in the review meeting, once both sides are in."
                    }
                review["next_goals"] = [
                    {"text": g} for g in _clean_goals(fields.get("next_goals"))
                ]
            if "pay" in params and isinstance(params.get("pay"), dict):
                problem = _apply_pay(review, params["pay"], ctx["viewer"], me)
                if problem:
                    return {"error": problem}
            _put_review(review)
            return {
                "ok": True,
                "missing": _manager_missing(review, _framework_for(review)),
            }

        if op == "review_save_pay":
            problem = _apply_pay(review, params.get("pay") or params, ctx["viewer"], me)
            if problem:
                return {"error": problem}
            _put_review(review)
            return {"ok": True}

        if op == "review_submit_manager":
            if not manager_open:
                return {"error": "Your part is already submitted."}
            _apply_feedback(review, params.get("fields") or {})
            if isinstance(params.get("pay"), dict):
                problem = _apply_pay(review, params["pay"], ctx["viewer"], me)
                if problem:
                    return {"error": problem}
            missing = _manager_missing(review, _framework_for(review))
            if missing:
                return {
                    "error": "Still to finish: " + " and ".join(missing),
                    "missing": missing,
                }
            review["manager_submitted_at"] = _now()
            review["status"] = (
                "ready_for_meeting" if st["employee"] else "employee_in_progress"
            )
            _put_review(review)
            return {"ok": True, "status": review["status"]}

        if op == "review_complete":
            """Finish the review: save the agreed goals and the pay decision,
            email the goals (cc the manager), mark it completed. A send that
            FAILS leaves the review open, as Orbit. While this app's email is
            switched off the review still completes — the result says the
            email didn't go."""
            if not st["both"]:
                return {
                    "error": "Both sides must be submitted before the review can be completed."
                }
            goals = _clean_goals(params.get("next_goals"))
            review["next_goals"] = [{"text": g} for g in goals]
            if isinstance(params.get("pay"), dict):
                problem = _apply_pay(review, params["pay"], ctx["viewer"], me)
                if problem:
                    return {"error": problem}
            to = str(params.get("to") or "").strip()
            cc = str(params.get("cc") or "").strip()
            subject = str(params.get("subject") or "").strip() or _GOALS_SUBJECT
            html = _goals_email_html(params.get("intro"), goals, params.get("sign_off"))
            review["summary_email_html"] = html
            result = {"ok": True, "email": None}
            if to:
                res = email.send(
                    [to],
                    subject,
                    html,
                    key="goals:"
                    + review["id"]
                    + ":"
                    + str(int(review.get("reopened_count") or 0)),
                    kind="goals",
                    cc=[cc] if cc else None,
                    reply_to=cc or None,
                    heading="Performance Review",
                )
                status = res.get("status")
                if status == "failed":
                    return {
                        "error": "The goals email couldn't be sent ("
                        + str(res.get("reason") or "unknown error")
                        + ") — the review is still open."
                    }
                result["email"] = status
                result["email_reason"] = res.get("reason")
                if res.get("refused"):
                    result["refused"] = res.get("refused")
                if email.mode != "live":
                    # A preview or a test to yourself: show the email, leave
                    # the review exactly as it was.
                    result["preview"] = True
                    result["html"] = res.get("html")
                    return result
                if status == "sent":
                    review["goals_email_sent_at"] = _now()
                    review["goals_email_to"] = to
            elif email.mode != "live":
                return {"ok": True, "email": None, "preview": True}
            review["status"] = "completed"
            review["completed_at"] = _now()
            review.pop("completed_by", None)
            review.update(_actor_fields("completed_by", ctx["viewer"], me))
            _put_review(review)
            return result

    if op in (
        "review_set_manager",
        "review_set_framework",
        "review_delete",
        "review_restore",
    ):
        review, ctx = _one_review(params)
        access = ctx["access"]
        if not access["admin"]:
            return {"error": "Only an HR admin can do that."}
        if access["reviewee"]:
            return {
                "error": "That's your own review — another admin needs to change it."
            }

        if op == "review_set_manager":
            """Change who manages this review. "This review only" sets the
            review's own manager; "Review + employee profile" also updates the
            person. "No manager" for this review only is recorded explicitly,
            so the profile manager doesn't quietly apply anyway."""
            new_manager = params.get("manager_id") or None
            scope = params.get("scope") or "review"
            if new_manager:
                if new_manager == review.get("person_id"):
                    return {"error": "Someone can't manage their own review."}
                store.get("people", new_manager)
            review["manager_id"] = new_manager
            review["manager_cleared"] = new_manager is None and scope == "review"
            if scope == "profile":
                person = store.get("people", review.get("person_id"))
                data = _strip(person)
                data["manager_id"] = new_manager
                store.put("people", data, person["id"])
                if new_manager is None:
                    review["manager_cleared"] = False
            _put_review(review)
            return {"ok": True}

        if op == "review_set_framework":
            """Switch the framework. Ratings are keyed by capability, so the old
            framework's answers stay (hidden) and come back if it's chosen
            again."""
            framework_id = params.get("framework_id") or None
            if framework_id:
                store.get("capability_frameworks", framework_id)
            review["framework_id"] = framework_id
            _put_review(review)
            return {"ok": True}

        if op == "review_delete":
            """Soft delete: answers, ratings and goals are kept; Restore brings
            it back."""
            review["deleted_at"] = _now()
            review.pop("deleted_by", None)
            review.update(_actor_fields("deleted_by", ctx["viewer"], ctx["me"]))
            _put_review(review)
            return {"ok": True}

        if op == "review_restore":
            for key in (
                "deleted_at",
                "deleted_by",
                "deleted_by_name",
                "deleted_by_orbit_id",
                "withdrawn",
            ):
                review.pop(key, None)
            _put_review(review)
            return {"ok": True}

    if op == "review_start":
        """Start a review by hand: employee, framework, due date (Orbit's
        "Start Review"), carrying goals forward from their last review."""
        if not _is_review_admin(viewer):
            return {"error": "Only an HR admin can start a review."}
        person_id = params.get("person_id")
        framework_id = params.get("framework_id")
        due = str(params.get("due_date") or "")[:10]
        if not person_id or not framework_id or not due:
            return {"error": "Pick the employee, the framework and the due date."}
        try:
            _date(due)
        except Exception:
            return {"error": "That due date isn't a date."}
        person = store.get("people", person_id)
        store.get("capability_frameworks", framework_id)
        review = _create_review(
            _new_review(
                person_id, framework_id, due, None, store.list("performance_reviews")
            ),
            person,
        )
        return {"review": {"id": review["id"]}}

    # ---- Review cycles ----------------------------------------------------

    if op in (
        "review_cycles",
        "save_review_cycle",
        "delete_review_cycle",
        "cycle_preview",
        "cycle_apply",
        "auto_enroll_preview",
    ) and not _is_review_admin(viewer):
        return {"error": "Only an HR admin can manage review cycles."}

    if op == "set_norm_runs_cycles":
        """Turn Norm's nightly cycle enrolment on (at cut-over) or off."""
        if not _is_review_admin(viewer):
            return {"error": "Only an HR admin can change this."}
        row = _app_settings()
        data = _strip(row) if row else {}
        data["norm_runs_cycles"] = bool(params.get("on"))
        data["norm_runs_cycles_set_by"] = viewer.get("name")
        data["norm_runs_cycles_set_at"] = _now()
        store.put("app_settings", data, row["id"] if row else None, venue_id="")
        return {"norm_runs_cycles": data["norm_runs_cycles"]}

    if op == "review_cycles":
        """Every cycle, each with a dry run of tonight's enrolment."""
        ctx = _reviews_ctx(params)
        today = _today()
        cycles = sorted(
            store.list("review_cycles"), key=lambda c: str(c.get("name") or "")
        )
        rows = []
        for c in cycles:
            plan = (
                _cycle_plan(c, ctx, today, True) if c.get("is_active", True) else None
            )
            rows.append({**c, "plan": plan})
        frameworks = sorted(
            [
                {"id": f["id"], "name": f.get("name")}
                for f in store.list("capability_frameworks")
            ],
            key=lambda f: str(f.get("name") or ""),
        )
        people = sorted(
            [
                {
                    "id": p["id"],
                    "name": p.get("name"),
                    "role": p.get("role") or "team_member",
                    "loadedhub_role": p.get("loadedhub_role"),
                    "payroll_company_id": p.get("payroll_company_id"),
                    "venues": p.get("venues") or [],
                }
                for p in ctx["people"]
                if _active(p)
            ],
            key=lambda p: str(p.get("name") or ""),
        )
        return {
            "cycles": rows,
            "frameworks": frameworks,
            "people": people,
            "today": today,
            "norm_runs_cycles": _norm_runs_cycles(),
            "is_admin": _is_review_admin(viewer),
        }

    if op == "save_review_cycle":
        """Create or edit a cycle. Orbit reset the rolled-forward dates to
        just the first date on every edit; here they're kept unless the first
        date itself changes."""
        f = params.get("fields") or {}
        cycle_id = params.get("cycle_id")
        name = str(f.get("name") or "").strip()
        if not name:
            return {"error": "A cycle needs a name."}
        first = str(f.get("first_date") or "")[:10]
        try:
            _date(first)
        except Exception:
            return {"error": "Pick a first review date."}
        cadence = str(f.get("cadence") or "annual")
        if not _parse_cadence(cadence):
            return {"error": "That cadence isn't one Norm understands."}
        try:
            lead = max(
                0,
                int(f.get("lead_days") if f.get("lead_days") not in (None, "") else 28),
            )
        except Exception:
            return {"error": "Lead days must be a number."}
        if cycle_id:
            data = _strip(store.get("review_cycles", cycle_id))
            dates = sorted({str(d)[:10] for d in data.get("due_dates") or [] if d})
            if not dates or dates[0] != first:
                dates = [first]
        else:
            data = {"is_active": True, "assignments": []}
            dates = [first]
        framework_id = f.get("default_framework_id") or None
        if framework_id:
            store.get("capability_frameworks", framework_id)
        data.update(
            {
                "name": name,
                "cadence": cadence,
                "default_framework_id": framework_id,
                "due_dates": dates,
                "lead_days": lead,
            }
        )
        if "is_active" in f:
            data["is_active"] = bool(f.get("is_active"))
        return {"cycle": store.put("review_cycles", data, cycle_id)}

    if op == "delete_review_cycle":
        """A cycle goes; its reviews stay (they keep their own due dates)."""
        store.delete("review_cycles", params.get("cycle_id"))
        return {"ok": True}

    if op in ("cycle_preview", "cycle_apply"):
        """The Assign dialog. `cycle_preview` is the "Before we save" dry run
        of the PROPOSED assignments and filter — nothing is written (Orbit
        saved the assignments first, so "Back" didn't undo them).
        `cycle_apply` saves them and enrols now."""
        cycle = store.get("review_cycles", params.get("cycle_id"))
        proposed = dict(cycle)
        if "person_ids" in params:
            keep = {
                a.get("person_id"): a.get("framework_id")
                for a in cycle.get("assignments") or []
            }
            proposed["assignments"] = [
                {"person_id": pid, "framework_id": keep.get(pid)}
                for pid in params.get("person_ids") or []
                if pid
            ]
        if "auto_enroll_filter" in params:
            f = params.get("auto_enroll_filter")
            proposed["auto_enroll_filter"] = f if isinstance(f, dict) else None
        ctx = _reviews_ctx(params)
        today = _today()
        if op == "cycle_preview":
            return {"plan": _cycle_plan(proposed, ctx, today, True)}
        saved = store.put("review_cycles", _strip(proposed), cycle["id"])
        if not _norm_runs_cycles():
            # Orbit is still the live system and creates each round's reviews
            # itself — Norm enrolling now would make a second set (and could
            # withdraw Orbit's). Save the assignments; show what would happen.
            return {
                "plan": _cycle_plan(saved, ctx, today, True),
                "saved_only": True,
                "message": "Saved. Norm will create these reviews once it runs "
                "cycles — while Orbit is the live system, Orbit creates them.",
            }
        return {"plan": _cycle_plan(saved, ctx, today, False)}

    if op == "auto_enroll_preview":
        """Who an auto-enrol filter matches (active people only)."""
        f = params.get("auto_enroll_filter")
        people = store.list("people")
        hits = [p for p in people if _active(p) and _filter_match(p, f)]
        hits.sort(key=lambda p: str(p.get("name") or ""))
        return {
            "count": len(hits),
            "people": [
                {"id": p["id"], "name": p.get("name"), "role": p.get("role")}
                for p in hits[:200]
            ],
        }

    # ---- The nightly run ----------------------------------------------------

    if op == "nightly":
        """Once a day per org (performance.json `schedules`, 19:00 UTC = 08:00 in
        NZ, as Orbit): roll every active cycle forward and enrol, then send
        the managers' digests. Runs as Norm itself; nobody can start it."""
        if not viewer.get("system"):
            return {"error": "The nightly run is Norm's own — it runs on its schedule."}
        slot = str((params.get("_schedule") or {}).get("slot") or _today())
        today = slot[:10]
        ctx = _reviews_ctx(params)
        summary = {"slot": slot, "cycles": []}
        runs_cycles = _norm_runs_cycles()
        if not runs_cycles:
            summary["cycles_skipped"] = (
                "Norm isn't creating cycle reviews yet (Orbit is live)"
            )
        for c in sorted(
            store.list("review_cycles") if runs_cycles else [],
            key=lambda c: str(c.get("name") or ""),
        ):
            if not c.get("is_active", True):
                continue
            plan = _cycle_plan(c, ctx, today, False)
            summary["cycles"].append(
                {
                    "cycle_id": c["id"],
                    "created": len(plan["created"]),
                    "removed": len(plan["removed"]),
                    "deletion_blocked": len(plan["deletion_blocked"]),
                    "added_due_dates": len(plan["added_due_dates"]),
                    "error": plan.get("error"),
                }
            )
        digests = _send_digests(ctx, today, slot, "live", viewer)
        summary["digests"] = {
            "planned": digests["planned"],
            "statuses": digests["statuses"],
        }
        return summary

    # ---- Review emails (the log page) ---------------------------------------

    if op in (
        "review_emails",
        "review_email_preview",
        "review_email_toggle",
        "review_digest_run",
    ) and not _is_review_admin(viewer):
        return {"error": "Only an HR admin can see the review emails."}

    if op == "review_emails":
        """Norm's sends (the platform's email log for this app) plus the
        history that came across from Orbit."""
        people = _index(store.list("people"))
        orbit_rows = []
        for e in store.list("performance_review_emails"):
            if not e.get("orbit_id"):
                continue
            orbit_rows.append(
                {
                    "id": e["id"],
                    "source": "orbit",
                    "at": e.get("sent_at") or e.get("created_at"),
                    "name": (people.get(e.get("person_id")) or {}).get("name"),
                    "to": e.get("to_email"),
                    "kind": e.get("kind"),
                    "is_test": bool(e.get("is_test")),
                    "subject": e.get("subject"),
                    "status": e.get("status"),
                    "error": e.get("error"),
                }
            )
        norm_rows = [
            {
                "id": e.get("id"),
                "source": "norm",
                "at": e.get("at"),
                "name": None,
                "to": ", ".join(e.get("to") or []),
                "kind": e.get("kind"),
                "is_test": bool(e.get("is_test")),
                "subject": e.get("subject"),
                "status": e.get("status"),
                "error": e.get("error"),
            }
            for e in email.log(300)
        ]
        rows = norm_rows + orbit_rows
        rows.sort(key=lambda r: str(r.get("at") or ""), reverse=True)
        return {
            "enabled": email.enabled(),
            "can_toggle": bool(viewer.get("is_admin"))
            or "org:manage" in (viewer.get("permissions") or []),
            "rows": rows[:500],
        }

    if op == "review_email_preview":
        if params.get("source") == "norm":
            return {"html": email.preview(params.get("id"))}
        row = store.get("performance_review_emails", params.get("id"))
        return {"html": row.get("body_html"), "error": row.get("error")}

    if op == "review_email_toggle":
        return email.set_enabled(bool(params.get("enabled")))

    if op == "review_digest_run":
        """"Preview today's emails" (dry_run) and "Send test to me" (test).
        Live digests only go from the nightly run."""
        mode = params.get("_email_mode")
        if mode not in ("dry_run", "test"):
            return {
                "error": "Preview or send a test — live digests go out on the nightly run."
            }
        ctx = _reviews_ctx(params)
        today = _today()
        return _send_digests(ctx, today, today, mode, viewer)

    return {"error": f"unknown op '{op}'"}
