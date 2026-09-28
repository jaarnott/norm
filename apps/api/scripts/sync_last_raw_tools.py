"""The last raw tools leave the agent menu: get_stocktakes and get_hr.

Phase 1 of finishing "no raw endpoints as tools" (Sep 2026). After Apps v3
(cfb9378) the App Map is the agent's exposure list, and only two families of
raw rows were still claimed:

- Loaded Stock claimed `loadedhub.generate_stocktake_report` (Loaded's report:
  274–566 raw lines the agent paged through with 7+ search_tool_result calls)
  and Orbit's `cook_brothers_app.stock_find_stocktakes` (the list of counts).
  `loadedhub.get_stocktakes` replaces both: view 'list', and view 'variance',
  which pairs two counts of the SAME template itself and returns totals, top
  shortfalls/surpluses and unlinked items apart.
- BambooHR claimed six raw reads, two of which never worked (list_employees
  was page 1 of 1,504 with 245 of 250 gone; get_employee returned only an id).
  `bamboohr.get_hr` replaces them: views employees / jobs / applications.
  `get_applicant_resume` stays — it is an internal handler that hands the
  model the CV itself, not a raw row.

Two stages, because the App Map claims live in scripts/sync_marketplace_catalog.py:

    1. uv run python scripts/sync_last_raw_tools.py            (install)
       adds the two consolidators and the two BambooHR backend rows they
       call (get_employee_detail, get_applications_query — both engine-only,
       verified live against the account on 28 Sep). Unclaimed, so invisible
       to every agent until step 2.
    2. uv run python scripts/sync_marketplace_catalog.py        (claims)
       Loaded Stock and BambooHR now claim the new tools instead.
    3. uv run python scripts/sync_last_raw_tools.py            (retire)
       now that nothing claims them: the replaced raw rows go engine-only
       (execution is untouched — the Hiring page calls get_jobs /
       get_applications / get_application_details by name and keeps working;
       get_stocktakes calls generate_stocktake_report), their binding entries
       are dropped, and the two skills and the HR prompt stop naming them.

`stock_find_stocktakes` is Orbit's tool, re-discovered from Orbit's server, so
it is unclaimed rather than flagged — a re-discovery would reset the flag.

Usage:
    uv run python scripts/sync_last_raw_tools.py [--dry-run]
"""

from __future__ import annotations

import copy
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

_DIR = pathlib.Path(__file__).resolve().parent.parent / "config" / "consolidators"

STOCKTAKES_TOOL = {
    "action": "get_stocktakes",
    "method": "GET",  # read-only consolidator: auto-executes
    "read_only": True,
    "description": (
        "Stocktakes for the venue. view 'list' (default with no template): "
        "completed counts with their template and date. view 'variance': pass "
        "a template ('Food', 'Beverage', or a template name) and optionally a "
        "period in plain English — Norm picks the two counts of THAT template "
        "itself (the last before the period and the last within it, or simply "
        "the latest two) and returns sales, COGS, total variance, the biggest "
        "shortfalls and surpluses by value, and items with no POS link "
        "reported as usage. Never pair counts yourself; ad-hoc counts are never "
        "paired. Opening/closing ids are accepted only when the user gives them."
    ),
    "required_fields": [],
    "optional_fields": [
        "view",
        "template",
        "period",
        "opening_id",
        "closing_id",
        "group",
        "query",
        "top",
        "sort_by",
        "status",
        "limit",
    ],
    "field_descriptions": {
        "view": "'list' | 'variance'. Defaults to 'variance' when a template or ids are given.",
        "template": "A template name — 'Food', 'Beverage', 'Other Stock' or e.g. 'DAILY STOCKTAKE - KITCHEN'.",
        "period": "Plain English — 'last month', 'August'. variance: opens on the last count before it.",
        "opening_id": "variance: a stocktake id the user named (with closing_id).",
        "closing_id": "variance: the later stocktake id the user named.",
        "group": "variance: only items in stock groups containing this ('Meats').",
        "query": "variance: only items whose name contains this.",
        "top": "variance: rows shown before the '(others)' rollup (default 15).",
        "sort_by": "variance: 'value' (default) or 'qty'.",
        "status": "list: 'completed' (default), 'pending' or 'all'.",
        "limit": "list: max rows (default 20).",
    },
    "field_schema": {
        "view": {"type": "string", "enum": ["list", "variance"]},
        "top": {"type": "integer"},
        "limit": {"type": "integer"},
    },
    "max_result_chars": 30_000,
    "consolidator_config": {
        # resolve (1) + Orbit list (1) + report (1) + its one retry (1).
        "max_api_calls": 4,
        "allowed_write_actions": [],
    },
}

HR_TOOL = {
    "action": "get_hr",
    "method": "GET",  # read-only consolidator: auto-executes
    "read_only": True,
    "description": (
        "BambooHR. view 'employees' (default): current staff with job title "
        "and venue — filter by query (name or role) or location (venue); "
        "employee_id for one person's HR record. view 'jobs': open roles with "
        "applicant counts (status 'all', 'filled', 'on hold' for others). view "
        "'applications': candidates newest first — by job_id, status ('new', "
        "'active', 'hired', or a stage like 'Reviewed') or query (a name); "
        "application_id for one application in full, including the CV's file "
        "id for get_applicant_resume. Hiring questions: jobs first, then that "
        "job's applications."
    ),
    "required_fields": [],
    "optional_fields": [
        "view",
        "query",
        "location",
        "employee_id",
        "status",
        "job_id",
        "application_id",
        "page",
        "limit",
    ],
    "field_descriptions": {
        "view": "'employees' | 'jobs' | 'applications'.",
        "query": "employees: name or job title; jobs: title; applications: applicant name.",
        "location": "employees / jobs: venue name, e.g. 'La Zeppa'.",
        "employee_id": "employees: one person's record.",
        "status": "jobs: open (default) | on hold | filled | all. applications: new | active | hired | inactive | all, or a stage name.",
        "job_id": "applications: one job's candidates.",
        "application_id": "applications: one application in full.",
        "page": "applications: page number (50 per page).",
        "limit": "employees / jobs: max rows (default 50).",
    },
    "field_schema": {
        "view": {"type": "string", "enum": ["employees", "jobs", "applications"]},
        "page": {"type": "integer"},
        "limit": {"type": "integer"},
    },
    "max_result_chars": 30_000,
    "consolidator_config": {
        # applications with a stage name: statuses (1) + query (1).
        "max_api_calls": 3,
        "allowed_write_actions": [],
    },
}

_EMPLOYEE_FIELDS = (
    "displayName,firstName,lastName,preferredName,jobTitle,department,location,"
    "division,status,employmentHistoryStatus,hireDate,originalHireDate,"
    "terminationDate,workEmail,mobilePhone,supervisor"
)
#: New BambooHR backend rows, cloned from an existing row (auth, headers) with
#: a new path. Both verified live on 28 Sep 2026.
BAMBOO_BACKENDS = {
    "get_employee_detail": (
        "get_employee",
        {
            "path_template": "/employees/{{ employee_id }}?fields=" + _EMPLOYEE_FIELDS,
            "required_fields": ["employee_id"],
            "description": (
                "[engine-only] One employee's HR record with named fields (a bare "
                "/employees/{id} returns only the id). Backend of get_hr."
            ),
        },
    ),
    "get_applications_query": (
        "get_applications",
        {
            "path_template": (
                "/applicant_tracking/applications?page={{ page | default(1) }}"
                "{% if job_id %}&jobId={{ job_id }}{% endif %}"
                "{% if status_id %}&applicationStatusId={{ status_id }}{% endif %}"
                "{% if status_group %}&applicationStatus={{ status_group }}{% endif %}"
                "{% if job_status %}&jobStatusGroups={{ job_status }}{% endif %}"
                "{% if search %}&searchString={{ search | urlencode }}{% endif %}"
                "&sortBy=created_date&sortOrder=DESC"
            ),
            "required_fields": [],
            "optional_fields": [
                "page",
                "job_id",
                "status_id",
                "status_group",
                "job_status",
                "search",
            ],
            "description": (
                "[engine-only] Applications, newest first, filtered by job, "
                "status id or group, job status group and name search, paged. "
                "Backend of get_hr."
            ),
        },
    ),
}

#: (connector, action) -> the tool that superseded it on the agent menu.
RETIRE = {
    ("loadedhub", "generate_stocktake_report"): "get_stocktakes (view 'variance')",
    ("bamboohr", "get_jobs"): "get_hr (view 'jobs')",
    ("bamboohr", "get_applications"): "get_hr (view 'applications')",
    ("bamboohr", "get_application_details"): "get_hr (application_id)",
    ("bamboohr", "get_applicant_statuses"): "get_hr (view 'applications', status)",
    ("bamboohr", "list_employees"): "get_hr (view 'employees')",
    ("bamboohr", "get_employee"): "get_hr (employee_id)",
}
#: Unclaimed only (Orbit re-discovery resets flags); its binding entry goes.
UNBIND_ONLY = {("cook_brothers_app", "stock_find_stocktakes")}

SKILLS = {
    "stocktake_variance": """## Stocktake & Variance

### Step 1: Run the variance
- Call `get_stocktakes` with view 'variance' and the template the user means ('Food', 'Beverage', 'Other Stock', or a template name). If they named a period ("August", "last month") pass it as `period`.
- Norm picks the two counts itself — two counts of that same template, never ad-hoc counts. If the user gave two stocktake ids, pass them as opening_id and closing_id instead.
- If it says there aren't two counts, tell the user what exists (view 'list' shows every count and its template). Never pair counts yourself.

### Step 2: Summarise
- Lead with sales, COGS % and the total variance for the window (opening and closing dates).
- Show the biggest shortfalls (negative variance) and surpluses by value — the `variances` rows. A shortfall is stock that went missing beyond what sales explain: waste, over-portioning or theft.
- Items with no POS link (`not_pos_linked`) have no expected use, so their figure is usage, not variance — list them separately if they matter.
- If the result carries a `warning`, repeat it: the two counts may not cover the same stock.

### Step 3: Current stock (if asked)
- Call `get_stock` with view 'on_hand' and the template for current positions.

### Notes
- Variances are normal in hospitality — focus on outliers.
- Narrow with `group` ('Meats') or `query` (an item name) rather than asking for everything.""",
    "candidate_review": """## Candidate Review

### Step 1: Find the job
- Call `get_hr` with view 'jobs' (open roles, with applicant counts). If the user named a role or venue, pass it as `query` or `location`.

### Step 2: Get the candidates
- Call `get_hr` with view 'applications' and the job_id. Add `status` ('new', 'active', or a stage like 'Reviewed') if the user asked for a stage; 50 per page — ask for `page` 2 when `more` is true.
- Today's date is already in your context — use it for a "recent" cutoff.

### Step 3: Review each candidate
- Call `get_hr` with application_id for their full application (questions, availability, desired pay).
- Call `get_applicant_resume` with the application's resume file_id to read their CV.
- Make these calls in parallel for multiple candidates.

### Step 4: Summarise
For each candidate provide:
1. **Name** and application date
2. **Key qualifications** — relevant experience, skills, certifications
3. **Strengths** — what makes them a good fit
4. **Concerns** — gaps, missing requirements, red flags
5. **Recommendation** — Strong Yes / Yes / Maybe / No

### Notes
- Be objective and fact-based.
- Focus on hospitality-relevant experience.
- Present candidates in order of recommendation strength (best first).""",
}
#: A skill is replaced only while it still names a retired tool.
_SKILL_OLD_MARKERS = {
    "stocktake_variance": ("generate_stocktake_report", "stock_find_stocktakes"),
    "candidate_review": ("get_jobs", "get_applications", "get_application_details"),
}

PROMPT_PATCHES = {
    "hr": [
        (
            "- For hiring queries, start with get_jobs to find the role, then "
            "get_applications for candidates.",
            "- For hiring queries, call get_hr with view 'jobs' to find the role, "
            "then view 'applications' with its job_id for candidates.",
        )
    ],
}


def _demoted_prefix(to: str) -> str:
    return f"[consolidator-only] Superseded by {to}. "


def main(dry_run: bool = False) -> None:
    from sqlalchemy.orm.attributes import flag_modified

    from app.db.config_models import (
        AgentConfig,
        AgentConnectionBinding,
        ConnectionSpec,
        Playbook,
    )
    from app.db.engine import _ConfigSessionLocal
    from app.services.entitlements import tool_owners

    db = _ConfigSessionLocal()
    changes: list[str] = []
    try:
        specs = {
            s.connector_name: s
            for s in db.query(ConnectionSpec)
            .filter(ConnectionSpec.connector_name.in_(["loadedhub", "bamboohr"]))
            .all()
        }
        if set(specs) != {"loadedhub", "bamboohr"}:
            raise SystemExit(
                f"specs missing: {sorted({'loadedhub', 'bamboohr'} - set(specs))}"
            )

        # ── 1. Install: consolidators + BambooHR backends ──────────────────
        installs = {
            "loadedhub": [(STOCKTAKES_TOOL, "get_stocktakes.py")],
            "bamboohr": [(HR_TOOL, "get_hr.py")],
        }
        for cn, spec in specs.items():
            tools = [dict(t) for t in (spec.tools or [])]
            idx = {t.get("action"): i for i, t in enumerate(tools)}
            touched = False
            for tool_def, fname in installs[cn]:
                tool = copy.deepcopy(tool_def)
                tool["consolidator_config"]["function_code"] = (_DIR / fname).read_text(
                    encoding="utf-8"
                )
                a = tool["action"]
                if a in idx:
                    keep = tools[idx[a]].get("added_at")
                    if keep:
                        tool["added_at"] = keep
                    if tools[idx[a]] != tool:
                        tools[idx[a]] = tool
                        touched = True
                        changes.append(f"spec {cn}.{a}: updated")
                else:
                    tools.append(tool)
                    idx[a] = len(tools) - 1
                    touched = True
                    changes.append(f"spec {cn}.{a}: added")
            if cn == "bamboohr":
                for action, (src, over) in BAMBOO_BACKENDS.items():
                    if src not in idx:
                        raise SystemExit(
                            f"bamboohr.{src} missing — cannot clone {action}"
                        )
                    row = copy.deepcopy(tools[idx[src]])
                    row.pop("added_at", None)
                    row.update(copy.deepcopy(over))
                    row.update(
                        {"action": action, "engine_only": True, "read_only": True}
                    )
                    if action in idx:
                        keep = tools[idx[action]].get("added_at")
                        if keep:
                            row["added_at"] = keep
                        if tools[idx[action]] != row:
                            tools[idx[action]] = row
                            touched = True
                            changes.append(f"spec bamboohr.{action}: updated")
                    else:
                        tools.append(row)
                        idx[action] = len(tools) - 1
                        touched = True
                        changes.append(f"spec bamboohr.{action}: added (engine-only)")
            if touched and not dry_run:
                spec.tools = tools
                flag_modified(spec, "tools")
                spec.version = (spec.version or 0) + 1

        # ── 1b. Orbit's list is a read ─────────────────────────────────────
        # Discovery filed every Orbit tool as POST (name didn't start with
        # "get_"), so the sandbox refused get_stocktakes' call to it as an
        # undeclared write. mcp_executor.is_read_tool fixes the next
        # re-discovery; this corrects the live row now.
        orbit = (
            db.query(ConnectionSpec)
            .filter(ConnectionSpec.connector_name == "cook_brothers_app")
            .first()
        )
        if orbit:
            otools = [dict(t) for t in (orbit.tools or [])]
            fixed = False
            for t in otools:
                if t.get("action") == "stock_find_stocktakes" and (
                    t.get("method") != "GET" or t.get("read_only") is not True
                ):
                    t["method"] = "GET"
                    t["read_only"] = True
                    fixed = True
                    changes.append(
                        "spec cook_brothers_app.stock_find_stocktakes: GET, read_only"
                    )
            if fixed and not dry_run:
                orbit.tools = otools
                flag_modified(orbit, "tools")
                orbit.version = (orbit.version or 0) + 1

        # ── 2. Retire — only once the App Map no longer claims the rows ────
        owned = tool_owners(db) or {}
        still_claimed = sorted(
            f"{c}.{a}"
            for (c, a) in list(RETIRE) + list(UNBIND_ONLY)
            if f"{c}.{a}" in owned
        )
        new_claimed = all(
            k in owned for k in ("loadedhub.get_stocktakes", "bamboohr.get_hr")
        )
        if still_claimed or not new_claimed:
            if not dry_run:
                db.commit()
            print(("DRY RUN — would apply:" if dry_run else "Applied:"))
            for line in changes or ["  (install: nothing to do)"]:
                print(f"  {line}")
            print(
                "\nRetire step skipped — the App Map still claims "
                f"{still_claimed or 'the old tools'}"
                + (
                    ""
                    if new_claimed
                    else " / does not yet claim get_stocktakes + get_hr"
                )
                + ".\nRun scripts/sync_marketplace_catalog.py, then this script again."
            )
            if dry_run:
                db.rollback()
            return

        for cn, spec in specs.items():
            tools = [dict(t) for t in (spec.tools or [])]
            touched = False
            for t in tools:
                to = RETIRE.get((cn, t.get("action")))
                if not to:
                    continue
                if not t.get("engine_only"):
                    t["engine_only"] = True
                    touched = True
                    changes.append(f"spec {cn}.{t['action']}: engine_only")
                desc = str(t.get("description") or "")
                if not desc.startswith(("[consolidator-only]", "[engine-only]")):
                    t["description"] = _demoted_prefix(to) + desc
                    touched = True
            if touched and not dry_run:
                spec.tools = tools
                flag_modified(spec, "tools")
                spec.version = (spec.version or 0) + 1

        drop = set(RETIRE) | UNBIND_ONLY
        for b in db.query(AgentConnectionBinding).all():
            caps = [dict(c) for c in (b.capabilities or [])]
            kept = [c for c in caps if (b.connector_name, c.get("action")) not in drop]
            if len(kept) != len(caps):
                gone = sorted(c.get("action") for c in caps if c not in kept)
                changes.append(
                    f"binding {b.agent_slug}/{b.connector_name}: dropped {gone}"
                )
                if not dry_run:
                    b.capabilities = kept
                    flag_modified(b, "capabilities")

        for slug, text in SKILLS.items():
            p = db.query(Playbook).filter(Playbook.slug == slug).first()
            if not p:
                print(f"WARNING: skill {slug} not found")
                continue
            if any(m in (p.instructions or "") for m in _SKILL_OLD_MARKERS[slug]):
                changes.append(f"skill {slug}: rewritten for the new tool")
                if not dry_run:
                    p.instructions = text

        for slug, patches in PROMPT_PATCHES.items():
            a = db.query(AgentConfig).filter(AgentConfig.agent_slug == slug).first()
            if not a or not a.system_prompt:
                continue
            text = a.system_prompt
            for old, new in patches:
                if old in text:
                    text = text.replace(old, new)
                    changes.append(f"prompt {slug}: needle swapped")
            if text != a.system_prompt and not dry_run:
                a.system_prompt = text

        if dry_run:
            db.rollback()
            print("DRY RUN — would apply:")
        else:
            db.commit()
            print("Applied:")
        for line in changes or ["  (nothing to do)"]:
            print(f"  {line}")

        if not dry_run:
            from app.services.config_validator import validate_config

            summary = validate_config(config_db=db)
            errors = [i for i in summary["issues"] if i.get("severity") == "error"]
            if errors:
                print("\nVALIDATION ERRORS (fix before walking away):")
                for i in errors:
                    print(f"  {i['where']}: {i['problem']}")
                sys.exit(1)
            print(
                f"\nconfig validation: clean ({summary['issue_count']} non-error notes)"
            )
    finally:
        db.close()


if __name__ == "__main__":
    main(dry_run="--dry-run" in sys.argv)
