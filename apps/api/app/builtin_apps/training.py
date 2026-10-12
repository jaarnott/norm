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


def _best_match(rows, want, label):
    """The row ``want`` names — its id, else its label: exact, then prefix,
    then anywhere, ignoring case. Norm opens the app by what people call
    things ("barista basics", "Sam"), never by our ids."""
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
        return email.link({"view": "my_team_reviews", "review": i["id"]})

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
        + "<li>Open it from the link above (or Training → Reviews → My team in Norm)</li>"
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
        "url": email.link({"view": "my_team_reviews"}),
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


def _plan_progress(plan, modules, sections, content):
    """Per module and per section: signed off / completed / in progress (x/y)
    / not started — from the matching assignment's completions and module
    sign-offs (Orbit's plan editor). Only completions that COUNT (not awaiting
    sign-off, not rejected) count — Orbit's editor counted every row."""
    match = [
        a
        for a in store.list("assignments", where={"program_id": plan.get("program_id")})
        if a.get("person_id") == plan.get("person_id")
        and (a.get("variant_id") or None) == (plan.get("variant_id") or None)
        and (a.get("venue_id") or None) == (plan.get("venue_id") or None)
    ]
    done = {}
    signed = {}
    if match:
        aid = match[0]["id"]
        for c in store.list("completions", where={"assignment_id": aid}):
            if _counts(c):
                done[c.get("content_id")] = str(
                    c.get("completed_at") or c.get("created_at") or ""
                )
        for s in store.list("module_signoffs", where={"assignment_id": aid}):
            signed[s.get("module_id")] = s.get("signed_off_at") or s.get("created_at")

    def status(items, signed_at=None, plan_done=False, plan_done_at=None):
        if signed_at:
            return {"label": "Signed off", "tone": "ok", "at": signed_at}
        dates = sorted(done[c["id"]] for c in items if c["id"] in done)
        if plan_done:
            # The trainer ticked the section off on the plan.
            return {"label": "Completed", "tone": "ok", "at": plan_done_at}
        if items and len(dates) == len(items):
            return {"label": "Completed", "tone": "ok", "at": dates[-1] or None}
        if dates:
            return {
                "label": "In progress ("
                + str(len(dates))
                + "/"
                + str(len(items))
                + ")",
                "tone": "warn",
                "at": None,
            }
        return {"label": "Not started", "tone": "", "at": None}

    by_module = _group(content, "module_id")
    by_section = _group(content, "section_id")
    return {
        "assignment_id": match[0]["id"] if match else None,
        "module": lambda m: status(by_module.get(m["id"], []), signed.get(m["id"])),
        "section": lambda s, ps: status(
            by_section.get(s["id"], []),
            None,
            ps.get("status") == "completed",
            ps.get("completed_at"),
        ),
    }


def run(params, call_api, log):
    op = (params or {}).get("op") or "overview"
    venues = {v.get("id"): v.get("name") for v in (params or {}).get("venues") or []}
    viewer = _viewer(params)
    me = None
    if op in ("sign_off", "reject_signoff", "enrol", "bulk_enrol", "create_plan"):
        me = _me(
            viewer,
            store.list("people", where={"norm_user_id": viewer.get("user_id")})
            if viewer.get("user_id")
            else [],
        )

    if op == "locate":
        """Where Norm asked the app to open (its component's inputs, see
        services/app_components.py): ``program`` and/or ``person``, as ids or
        names, resolved here where the data is. An active program wins a name
        tie. A person wins over a program — their page shows every program
        they're on."""
        out = {"program_id": None, "person_id": None, "review_id": None}
        if params.get("review"):
            # A review is opened by id (the digest and goals emails link to
            # one); whether this viewer may see it is the `review` op's call.
            try:
                out["review_id"] = store.get(
                    "performance_reviews", params.get("review")
                )["id"]
            except Exception:
                return {"error": "That performance review no longer exists."}
        if params.get("program"):
            programs = store.list("programs")
            programs.sort(key=lambda p: not p.get("is_active", True))
            program = _best_match(
                programs, params.get("program"), lambda p: p.get("name")
            )
            if program is None:
                return {
                    "error": f"No training program matching '{params.get('program')}'"
                }
            out["program_id"] = program["id"]
        if params.get("person"):
            person = _best_match(
                store.list("people"), params.get("person"), lambda p: p.get("name")
            )
            if person is None:
                return {
                    "error": f"Nobody in training matching '{params.get('person')}'"
                }
            out["person_id"] = person["id"]
        return out

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
            **_actor_fields("assigned_by", viewer, me),
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
                **_actor_fields("assigned_by", viewer, me),
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
        section_ids = {s["id"] for s in sections}
        content = [
            c for c in store.list("content") if c.get("section_id") in section_ids
        ]
        progress = _plan_progress(plan, modules, sections, content)
        rows = []
        module_rows = []
        for m in modules:
            secs = [x for x in sections if x.get("module_id") == m["id"]]
            if secs:
                module_rows.append(
                    {
                        "id": m["id"],
                        "name": m.get("name"),
                        "progress": progress["module"](m),
                    }
                )
            for s in secs:
                ps = scheduled.get(s["id"]) or {}
                rows.append(
                    {
                        "section_id": s["id"],
                        "module_id": m["id"],
                        "module": m.get("name"),
                        "name": s.get("name") or "Section",
                        "section_type": s.get("section_type"),
                        "plan_section_id": ps.get("id"),
                        "due_date": ps.get("due_date"),
                        "start_time": ps.get("start_time"),
                        "status": ps.get("status") or "pending",
                        "progress": progress["section"](s, ps),
                    }
                )
        person = {}
        if plan.get("person_id"):
            try:
                person = store.get("people", plan["person_id"])
            except Exception:
                person = {}
        return {
            "plan": {
                "id": plan_id,
                "program": program.get("name"),
                "program_id": plan.get("program_id"),
                "status": plan.get("status") or "active",
                "person_id": plan.get("person_id"),
                "person_name": person.get("name"),
                "venue_id": plan.get("venue_id"),
                "variant_name": plan.get("variant_name"),
                "instance": _instance_label(plan, venues),
                # A venue-sourced variant decides the venue itself (Orbit
                # hides the venue picker for those programs).
                "venue_from_variant": any(
                    (g or {}).get("source") == "venue"
                    for g in program.get("variants") or []
                ),
                "assignment_id": progress["assignment_id"],
            },
            "modules": module_rows,
            "sections": rows,
        }

    if op == "set_plan_venue":
        """Move a plan to another venue, and the enrolment it covers with it
        (Orbit's updatePlanVenue): the assignment for the same person, program
        and variant, matched on the plan's OLD venue."""
        plan_id = params.get("plan_id")
        plan = store.get("plans", plan_id)
        new_venue = params.get("venue_id") or None
        old_venue = plan.get("venue_id") or None
        if new_venue == old_venue:
            return {"plan": plan, "moved_assignments": 0}
        matching = [
            a
            for a in store.list(
                "assignments", where={"program_id": plan.get("program_id")}
            )
            if a.get("person_id") == plan.get("person_id")
            and (a.get("variant_id") or None) == (plan.get("variant_id") or None)
            and (a.get("venue_id") or None) == old_venue
        ]
        # venue_id="" is storage's explicit "group-wide" (no venue).
        target = new_venue or ""
        saved = store.put("plans", _strip(plan), plan_id, venue_id=target)
        for a in matching:
            store.put("assignments", _strip(a), a["id"], venue_id=target)
        return {"plan": saved, "moved_assignments": len(matching)}

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
                    **_actor_fields("created_by", viewer, me),
                },
                # "" = group-wide on purpose; None = the selected venue.
                venue_id=""
                if params.get("venue_id") == ""
                else (params.get("venue_id") or None),
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
        data.pop("signoff_by", None)
        data.update(_actor_fields("signoff_by", viewer, me))
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
        data.pop("rejected_by", None)
        data.update(_actor_fields("rejected_by", viewer, me))
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

    # ---- Orbit refresh banner --------------------------------------------

    if op == "sync_status":
        """When the Orbit import last brought data in: the newest row any
        refresh created (Orbit is live, so every refresh brings new rows).
        Rows Norm creates have no orbit_id and don't count."""
        latest = None
        for coll in (
            "completions",
            "assignments",
            "people",
            "performance_reviews",
            "plans",
            "programs",
            "review_cycles",
        ):
            rows = store.list(
                coll, where={"orbit_id": {"is_null": False}}, descending=True, limit=1
            )
            for r in rows:
                stamp = str(r.get("created_at") or "")
                if stamp and (latest is None or stamp > latest):
                    latest = stamp
        return {"refreshed_at": latest}

    # ---- People ---------------------------------------------------------

    if op == "people":
        """Everyone, with Orbit's Active (on payroll) / App only (not in
        LoadedHub) / Deactivated buckets and the manager picker's options."""
        people = store.list("people")
        pidx = _index(people)
        counts = {"active": 0, "app_only": 0, "deactivated": 0}
        rows = []
        for p in people:
            if not _active(p):
                bucket = "deactivated"
            elif p.get("loadedhub_id"):
                bucket = "active"
            else:
                bucket = "app_only"
            counts[bucket] += 1
            mid = p.get("manager_id")
            rows.append(
                {
                    "id": p["id"],
                    "name": p.get("name"),
                    "email": p.get("email"),
                    "role": p.get("role") or "team_member",
                    "role_label": _role_label(p),
                    "loadedhub_role": p.get("loadedhub_role"),
                    "venues": p.get("venues") or [],
                    "manager_id": mid,
                    "manager_name": (pidx.get(mid) or {}).get("name") if mid else None,
                    "is_active": _active(p),
                    "bucket": bucket,
                    "linked": bool(p.get("norm_user_id")),
                    "from_orbit": bool(p.get("orbit_id")),
                }
            )
        rows.sort(key=lambda r: str(r.get("name") or "").lower())
        return {
            "people": rows,
            "counts": counts,
            "managers": _manager_options(people)
            + [
                {
                    "id": p["id"],
                    "name": p.get("name"),
                    "role": p.get("role"),
                    "inactive": True,
                }
                for p in people
                if not _active(p)
                and any(x.get("manager_id") == p["id"] for x in people)
            ],
            "can_set_manager": _is_review_admin(viewer),
        }

    if op == "save_person":
        """Create or edit a person. "No manager" really clears (Orbit's update
        ignored a NULL manager, so one could never be removed). Changing a
        manager decides who sees and writes someone's performance review, so
        it is a review admin's call — and so is the rest of a person: their
        email decides where digests and goals emails go, deactivating them
        withdraws their cycle reviews, and their login link decides who they
        are to this app."""
        if not _is_review_admin(viewer):
            return {"error": "Only an HR admin can add or change people."}
        fields = params.get("fields") or {}
        person_id = params.get("person_id")
        if person_id:
            data = _strip(store.get("people", person_id))
        else:
            data = {"is_active": True, "role": "team_member"}
        for key in ("name", "email", "role", "is_active"):
            if key in fields:
                data[key] = fields[key]
        if "venues" in fields:
            data["venues"] = [str(v) for v in fields.get("venues") or [] if v]
        data["name"] = str(data.get("name") or "").strip()
        if not data["name"]:
            return {"error": "A person needs a name."}
        data["email"] = str(data.get("email") or "").strip() or None
        if data["email"] and ("@" not in data["email"] or " " in data["email"]):
            return {"error": "That email address doesn't look right."}
        if data.get("role") not in ("team_member", "manager", "head_office"):
            return {"error": "Role must be team member, manager or head office."}
        data["is_active"] = bool(data.get("is_active", True))
        if "manager_id" in fields:
            new_manager = fields.get("manager_id") or None
            if new_manager != (data.get("manager_id") or None):
                if not _is_review_admin(viewer):
                    return {
                        "error": "Only an HR admin can change someone's manager — "
                        "it decides who sees their performance review."
                    }
                if new_manager and new_manager == person_id:
                    return {"error": "Someone can't be their own manager."}
                if new_manager:
                    store.get("people", new_manager)
                data["manager_id"] = new_manager
        return {"person": store.put("people", data, person_id)}

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
            FAILS leaves the review open, as Orbit. While Training's email is
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
        """Once a day per org (training.json `schedules`, 19:00 UTC = 08:00 in
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
