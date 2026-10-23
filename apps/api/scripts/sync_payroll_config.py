"""Install the Loaded Payroll + Norm Finance config layer on the ``loadedhub``
spec — plan docs/plans/finance-migration.md §7 (F2 of the build).

What this puts in the shared config DB, idempotently:

  endpoint rows   nine engine-only Loaded calls (raw payloads, no transform —
                  a consolidator shapes what it reads): the payroll clock-in
                  feed, the pay summary, wages by role, cash-up variance, POS
                  voids, the two budget WRITES, the filtered leave-request read
                  and the one-person staff detail. The live
                  ``get_timeclock_entries`` and ``get_leave_requests`` rows are
                  untouched: a shared row's change goes live in every
                  environment on sync.
  tool rows       get_payrun (payroll:read), get_weekly_financials
                  (finance:read, the finance_weekly display component),
                  set_budgets (asks every time), its engine-only twin
                  set_budgets_typed (always-allowable), and the engine-only
                  get_closing_manager — code from config/consolidators/, the
                  budget twins with set_budgets_common.py prepended (the sandbox
                  allows no imports), each with the shapes it applies to the
                  endpoints it reads (config/consolidators/shapes.json).

Nothing here claims a tool for an App: exposure is the App Map
(scripts/sync_marketplace_catalog.py — Norm Finance claims
get_weekly_financials and set_budgets; Loaded Payroll claims get_payrun in
Phase 8). An unclaimed tool is an engine-side building block.

The config DB is SHARED WITH PRODUCTION. validate_config runs over the
pending state before anything is committed; ``--dry-run`` prints the diff and
writes nothing.

Usage (from apps/api, CONFIG_DATABASE_URL set):
    uv run python scripts/sync_payroll_config.py --dry-run
    uv run python scripts/sync_payroll_config.py
"""

from __future__ import annotations

import argparse
import json
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

CONNECTOR = "loadedhub"
CONSOLIDATORS = (
    pathlib.Path(__file__).resolve().parent.parent / "config" / "consolidators"
)
SHAPES_FILE = CONSOLIDATORS / "shapes.json"

_HEADERS = {
    "Content-Type": "application/json",
    "x-loaded-company-id": "{{ creds.x_loaded_company_id }}",
}
_HOST = "//loadedhub.com/api"

_ISO = "ISO 8601 with the venue's offset, e.g. 2026-10-12T07:00:00+13:00"
_DATE = "YYYY-MM-DD"
_INTERVAL = "Loaded interval, d.hh:mm:ss — 1.00:00:00 for daily buckets"


def _ep(
    action: str,
    method: str,
    path: str,
    description: str,
    *,
    required: tuple[str, ...] = (),
    optional: tuple[str, ...] = (),
    fields: dict[str, str] | None = None,
    body: str = "",
    success: tuple[int, ...] = (200,),
    read_only: bool = True,
    timeout: int = 60,
) -> dict:
    """One raw Loaded call, in the ``_ep`` shape of sync_xero_connector.py."""
    return {
        "action": action,
        "description": description,
        "method": method,
        "path_template": _HOST + path,
        "headers": dict(_HEADERS),
        "required_fields": list(required),
        "optional_fields": list(optional),
        "field_mapping": {f: f for f in (*required, *optional)},
        "field_descriptions": fields or {},
        "request_body_template": body,
        "success_status_codes": list(success),
        "response_ref_path": "",
        "timeout_seconds": timeout,
        "read_only": read_only,
        "engine_only": True,
    }


ENDPOINTS = [
    _ep(
        "get_payrun_clockins_raw",
        "GET",
        "/time-clockins?startTime={{ start_time }}&endTime={{ end_time }}"
        "&includeInactive=true&shouldTruncateShifts=false"
        "&includeCrossVenueShifts=true&filterByPayrollVenue=true"
        "{% if q %}&{{ q }}{% endif %}",
        "The payroll clock-in feed: every shift THIS company pays for in the "
        "window (Loaded's filterByPayrollVenue attribution, cross-venue shifts "
        "included), deleted and open clock-ins included so the caller can "
        "count and drop them. `q` is a raw query fragment for the repeated "
        "keys — shiftTypesToInclude=Clockin&shiftTypesToInclude=Roster&"
        "shiftTypesToInclude=DefaultHours&shiftTypesToInclude=Leave and "
        "staffMemberIds=… — which a field mapping cannot express. Raw rows "
        "carry payRateBreakdown[], rules[] (public holidays), venueId and "
        "isFromOtherVenue. Read by get_payrun.",
        required=("start_time", "end_time"),
        optional=("q",),
        fields={
            "start_time": _ISO,
            "end_time": _ISO,
            "q": "Raw query fragment of repeated keys (shiftTypesToInclude=…, "
            "staffMemberIds=…), already URL-safe",
        },
    ),
    _ep(
        "get_pay_summary_raw",
        "GET",
        "/payroll/pay-summary?startTime={{ start_time }}&endTime={{ end_time }}",
        "Loaded's own pay summary for the window: per staff hours rostered / "
        "worked, totalWages, allApproved, isLinkedToCurrentVenue; totalsByRole; "
        "total {hoursRostered, hoursWorked, totalWages}; leave[] and "
        "leaveTotal. The parity line get_payrun reports its rows against.",
        required=("start_time", "end_time"),
        fields={"start_time": _ISO, "end_time": _ISO},
    ),
    _ep(
        "get_clockins_summary_by_role_raw",
        "GET",
        "/time/clockins/summary/group-by?startTime={{ start_time }}"
        "&endTime={{ end_time }}&groupBy=role",
        "Worked cost and hours per role for the window: [{roleId, roleName, "
        "roleCostGroup (f food / b beverage), cost, hours}]. Orbit's "
        "Financial Report wages source. Read by get_weekly_financials.",
        required=("start_time", "end_time"),
        fields={"start_time": _ISO, "end_time": _ISO},
    ),
    _ep(
        "get_cashup_sets_summary_raw",
        "GET",
        "/cashup-sets/summary?start={{ start }}&end={{ end }}&interval={{ interval }}",
        "Cash-up variance per interval: [{startTime, totalVariance, "
        "cashVariance}] — Σ(counted − system) of the sets whose end falls in "
        "the bucket. Read by get_weekly_financials.",
        required=("start", "end", "interval"),
        fields={"start": _ISO, "end": _ISO, "interval": _INTERVAL},
    ),
    _ep(
        "get_pos_voids_raw",
        "GET",
        "/pos/voids?start={{ start }}&end={{ end }}&interval={{ interval }}",
        "POS voids and waste per interval: [{startTime, invoices, voidReason, "
        "voidQuantity, voidAmount, wasteQuantity, wasteAmount, transactions}]. "
        "Read by get_weekly_financials.",
        required=("start", "end", "interval"),
        fields={"start": _ISO, "end": _ISO, "interval": _INTERVAL},
    ),
    _ep(
        "update_budget_raw",
        "PUT",
        "/budgets/{{ budget_id }}",
        "WRITE: set one day's budget on an existing Loaded budget row. Loaded "
        "applies only `amount` (204 No Content); `date` and `salesTax` are "
        "echoed from the row itself. Only ever called by set_budgets / "
        "set_budgets_typed for a day that HAS a row.",
        required=("budget_id", "loaded_date", "amount", "sales_tax"),
        fields={
            "budget_id": "The Loaded budget row's id (from get_budgets_raw)",
            "loaded_date": "The row's own stored date, echoed",
            "amount": "GST-inclusive budget for the day",
            "sales_tax": "The row's own salesTax rate, echoed (e.g. 0.15)",
        },
        body=(
            '{"id": "{{ budget_id }}", "date": "{{ loaded_date }}", '
            '"amount": {{ amount }}, "salesTax": {{ sales_tax }}}'
        ),
        success=(204, 200),
        read_only=False,
        timeout=30,
    ),
    _ep(
        "create_budget_raw",
        "POST",
        "/budgets",
        "WRITE: create the budget row for a day Loaded has none for. `date` "
        "must be the stored instant — midnight AFTER the trading day in the "
        "venue's zone at that instant: (D+1)T00:00:00+13:00 for trading day D. "
        "Returns the new row {id, date, amount, salesTax}; a second POST for "
        "the same day is a 500 (unique date), and there is no DELETE. Only "
        "ever called by set_budgets / set_budgets_typed for a day with NO row.",
        required=("loaded_date", "amount"),
        fields={
            "loaded_date": "(D+1)T00:00:00<venue offset at that instant>",
            "amount": "GST-inclusive budget for the day",
        },
        body='{"date": "{{ loaded_date }}", "amount": {{ amount }}}',
        success=(200, 201),
        read_only=False,
        timeout=30,
    ),
    _ep(
        "get_leave_requests_filtered_raw",
        "GET",
        "/time/leave-requests?{{ q }}",
        "Leave requests with Loaded's full filter set riding a raw query "
        "fragment: from=YYYY-MM-DD&to=YYYY-MM-DD (to is exclusive), repeated "
        "staffMemberIds=…, statuses=Requested|Approved|Cancelled|Declined, "
        "requestedFrom / requestedTo. Rows: id, staffMemberId, leaveTypeId, "
        "leaveTypeName, startDateTime, endDateTime, status, dayLines[{date, "
        "hours}], actions[]. The live get_leave_requests (from/to only) is "
        "untouched. For the Leave page and the sick-leave checks.",
        required=("q",),
        fields={"q": "Raw query fragment (from=…&to=…&staffMemberIds=…&statuses=…)"},
    ),
    _ep(
        "get_staff_member_raw",
        "GET",
        "/staff-members/{{ staff_member_id }}",
        "ONE staff member's detail, for `datestampStarted` alone (the sick-leave "
        "qualification gate). The NZ detail also returns IRD, bank account and "
        "KiwiSaver fields: callers store datestampStarted only and the read is "
        "logged. Never run through /test on a live venue — the raw payload "
        "would land in the session.",
        required=("staff_member_id",),
        fields={"staff_member_id": "Loaded staff member id"},
        timeout=30,
    ),
]


def _schema(**fields: dict) -> dict:
    return dict(fields)


_PAYRUN_FIELDS = dict(
    venue="The payroll company (a Norm venue name, exact)",
    week_ending="The pay week's Sunday, YYYY-MM-DD (any date in the week snaps to it)",
    view="staff (default) | security | leave | salaried | hygiene | intercompany | all",
    venues="'all' — only with view 'intercompany' (every entity's cross-venue shifts)",
    rows="'false' for the sections only (chat); default true",
    weeks="Pay weeks ending week_ending to cover (1–26; the sick-leave look-back)",
    staff_member_ids="Only these Loaded staff ids (list or JSON text)",
    security_pay_code="The security contractors' pay code (default SECURITY)",
)
_WEEKLY_FIELDS = dict(
    week_ending="The week's Sunday, YYYY-MM-DD (default: the last completed week)",
    venues="'all', a list of venue names, or one name",
    venue="One venue's name",
    include_days="'false' to leave out the daily rows (default true)",
)
_BUDGET_FIELDS = dict(
    venue="The venue (Norm name, exact) — one venue per push",
    days='The days to set: [{"date": "YYYY-MM-DD", "amount": 2100}] — GST-inclusive, as Loaded stores budgets',
    allow_zero="'true' to send $0 days (default: $0 days are skipped and listed)",
    vintage_id="The forecast vintage the amounts came from (recorded on the ledger)",
)
_CLOSING_FIELDS = dict(
    venue="The venue's name",
    dates="Trading days to look up, YYYY-MM-DD (list, JSON text or comma text; ≤ 31)",
    roles="Loaded role names that close a venue (default ['Sales Manager']; matched trimmed, case-blind)",
)


def _str_schema(fields: dict[str, str]) -> dict:
    return {k: {"type": "string", "description": v} for k, v in fields.items()}


TOOLS = [
    {
        "action": "get_payrun",
        "method": "GET",
        "read_only": True,
        "effect": "read",
        "requires": ["payroll:read"],
        "description": (
            "A pay week for one payroll company, shaped for payroll: every shift "
            "the company pays (Loaded's own attribution, shifts worked at other "
            "venues included), with hours, cost, the public-holiday premium, "
            "pay code, cost group and the worked venue; deleted and unfinished "
            "clock-ins dropped and counted; a parity line against Loaded's pay "
            "summary. Views: staff (default), security, leave, salaried, hygiene "
            "(unapproved clock-ins, missing pay codes, parity), intercompany "
            "(cross-venue shifts; venues='all' for every entity's), all. "
            "week_ending is the pay week's Sunday."
        ),
        "required_fields": ["week_ending"],
        "optional_fields": [k for k in _PAYRUN_FIELDS if k != "week_ending"],
        "field_descriptions": _PAYRUN_FIELDS,
        "field_schema": {
            **_str_schema(_PAYRUN_FIELDS),
            "view": {
                "type": "string",
                "enum": [
                    "staff",
                    "security",
                    "leave",
                    "salaried",
                    "hygiene",
                    "intercompany",
                    "all",
                ],
                "description": _PAYRUN_FIELDS["view"],
            },
            "rows": {"type": "boolean", "description": _PAYRUN_FIELDS["rows"]},
            "weeks": {"type": "integer", "description": _PAYRUN_FIELDS["weeks"]},
            "staff_member_ids": {
                "type": "array",
                "items": {"type": "string"},
                "description": _PAYRUN_FIELDS["staff_member_ids"],
            },
        },
        "max_result_chars": 60_000,
        "consolidator_config": {
            # 8 reads serve one company; intercompany fans the clock-in and
            # role reads over every entity (2 + 2 × venues).
            "max_api_calls": 24,
            "allowed_write_actions": [],
            "function_file": "get_payrun.py",
        },
    },
    {
        "action": "get_weekly_financials",
        "method": "GET",
        "read_only": True,
        "effect": "read",
        "requires": ["finance:read"],
        "description": (
            "The Weekly Summary: per venue, the week and the month-to-date with "
            "sales incl and ex GST, budget and budget %, orders, discounts, "
            "voids, the food / beverage split, wages by cost group with wage %, "
            "rostered vs actual labour and the cash-up variance — plus the "
            "group totals and the week's daily rows. The weekly view renders "
            "this result; reply with what the tiles don't say. venues='all' "
            "for the group."
        ),
        "required_fields": [],
        "optional_fields": list(_WEEKLY_FIELDS),
        "field_descriptions": _WEEKLY_FIELDS,
        "field_schema": {
            **_str_schema(_WEEKLY_FIELDS),
            "venues": {"description": _WEEKLY_FIELDS["venues"]},
            "include_days": {
                "type": "boolean",
                "description": _WEEKLY_FIELDS["include_days"],
            },
        },
        "max_result_chars": 60_000,
        "display_component": "finance_weekly",
        "display_props": {"title": "Weekly summary"},
        "consolidator_config": {
            # list_venues + 13 per venue: two calendar resolves, six daily
            # feeds over the month-to-date, items and wages by role for both
            # windows, and the Norm budget plan — nine venues under the cap.
            "max_api_calls": 120,
            "allowed_write_actions": [],
            "function_file": "get_weekly_financials.py",
        },
    },
    {
        "action": "set_budgets",
        "method": "PUT",
        "read_only": False,
        "effect": "write",
        "requires": ["budgets:write"],
        # Asks every time: the numbers may come from a forecast, and a
        # per-person always-allow would carry them past the card (D6).
        "approval": {
            "default": "ask",
            "allow_auto": False,
            "label": "set daily budgets in Loaded",
        },
        "description": (
            "Set a venue's daily sales budgets in Loaded (GST-inclusive, as "
            "Loaded stores them): updates the day's row where one exists, "
            "creates it where none does; never more than 92 days; $0 days only "
            "with allow_zero. Previews every day — weekday, before, after, "
            "update or create — with a warning on any move over 25%, and asks "
            "for approval every time. Read get_budgets first."
        ),
        "required_fields": ["venue", "days"],
        "optional_fields": ["allow_zero", "vintage_id"],
        "field_descriptions": _BUDGET_FIELDS,
        "field_schema": {
            "venue": {"type": "string", "description": _BUDGET_FIELDS["venue"]},
            "days": {
                "type": "array",
                "description": _BUDGET_FIELDS["days"],
                "items": {
                    "type": "object",
                    "properties": {
                        "date": {"type": "string"},
                        "amount": {"type": "number"},
                    },
                    "required": ["date", "amount"],
                },
            },
            "allow_zero": {
                "type": "boolean",
                "description": _BUDGET_FIELDS["allow_zero"],
            },
            "vintage_id": {
                "type": "string",
                "description": _BUDGET_FIELDS["vintage_id"],
            },
        },
        "max_result_chars": 30_000,
        "consolidator_config": {
            "max_api_calls": 100,
            "allowed_write_actions": [
                "update_budget_raw",
                "create_budget_raw",
                "norm_finance.record_budget_push",
            ],
            "function_file": "set_budgets.py",
            "prepend": "set_budgets_common.py",
        },
    },
    {
        "action": "set_budgets_typed",
        "method": "PUT",
        "read_only": False,
        "effect": "write",
        "requires": ["budgets:write"],
        # Engine-only: the Budgets day grid's type→sync through the page
        # helper. A person typed the number, so always-allow per person is
        # permitted from day one.
        "engine_only": True,
        "approval": {
            "default": "ask",
            "allow_auto": True,
            "label": "sync typed budgets to Loaded",
        },
        "description": (
            "Engine-only twin of set_budgets for the Budgets day grid: the same "
            "write (update where a row exists, create where none; 92-day cap; "
            "$0 only with allow_zero; every day previewed), recorded as a "
            "manual push. Never shown to the model."
        ),
        "required_fields": ["venue", "days"],
        "optional_fields": ["allow_zero"],
        "field_descriptions": {
            k: v for k, v in _BUDGET_FIELDS.items() if k != "vintage_id"
        },
        "field_schema": {
            "venue": {"type": "string", "description": _BUDGET_FIELDS["venue"]},
            "days": {
                "type": "array",
                "description": _BUDGET_FIELDS["days"],
                "items": {
                    "type": "object",
                    "properties": {
                        "date": {"type": "string"},
                        "amount": {"type": "number"},
                    },
                    "required": ["date", "amount"],
                },
            },
            "allow_zero": {
                "type": "boolean",
                "description": _BUDGET_FIELDS["allow_zero"],
            },
        },
        "max_result_chars": 30_000,
        "consolidator_config": {
            "max_api_calls": 100,
            "allowed_write_actions": [
                "update_budget_raw",
                "create_budget_raw",
                "norm_finance.record_budget_push",
            ],
            "function_file": "set_budgets_typed.py",
            "prepend": "set_budgets_common.py",
        },
    },
    {
        "action": "get_closing_manager",
        "method": "GET",
        "read_only": True,
        "effect": "read",
        "engine_only": True,
        "description": (
            "Engine-only. Who closed the venue on each trading day: the latest-"
            "ending clock-in in the closing roles (default Sales Manager), else "
            "the latest-ending rostered shift; name, Loaded staff id, email and "
            "the source. The 08:00 entertainers job and the Roster column call "
            "it. Never shown to the model."
        ),
        "required_fields": ["dates"],
        "optional_fields": ["venue", "roles"],
        "field_descriptions": _CLOSING_FIELDS,
        "field_schema": {
            "venue": {"type": "string", "description": _CLOSING_FIELDS["venue"]},
            "dates": {
                "type": "array",
                "items": {"type": "string"},
                "description": _CLOSING_FIELDS["dates"],
            },
            "roles": {
                "type": "array",
                "items": {"type": "string"},
                "description": _CLOSING_FIELDS["roles"],
            },
        },
        "max_result_chars": 20_000,
        "consolidator_config": {
            # resolve_dates + the three existing rows, once each.
            "max_api_calls": 4,
            "allowed_write_actions": [],
            "function_file": "get_closing_manager.py",
        },
    },
]


def _function_code(cfg: dict) -> str:
    body = (CONSOLIDATORS / cfg["function_file"]).read_text(encoding="utf-8")
    if cfg.get("prepend"):
        common = (CONSOLIDATORS / cfg["prepend"]).read_text(encoding="utf-8")
        return common + "\n\n" + body
    return body


def _shapes_for(action: str) -> dict:
    """The transforms this tool applies to each endpoint it reads — the
    canonical record in shapes.json, so a re-run never drops them."""
    shapes = json.loads(SHAPES_FILE.read_text(encoding="utf-8"))
    return dict(shapes.get(f"{CONNECTOR}.{action}") or {})


def tool_rows() -> list[dict]:
    """TOOLS with their function_code and shapes installed."""
    rows = []
    for tool in TOOLS:
        row = {k: v for k, v in tool.items() if k != "consolidator_config"}
        cfg = {
            k: v
            for k, v in tool["consolidator_config"].items()
            if k not in ("function_file", "prepend")
        }
        cfg["function_code"] = _function_code(tool["consolidator_config"])
        cfg["shapes"] = _shapes_for(tool["action"])
        row["consolidator_config"] = cfg
        rows.append(row)
    return rows


def _merge(existing: list | None, ours: list[dict], keep_added_at: bool) -> list:
    """Upsert our rows by action; keep rows we don't own and each row's
    added_at (the listener stamps new ones)."""
    wanted = {row["action"]: row for row in ours}
    merged = []
    seen = set()
    for row in existing or []:
        action = row.get("action") if isinstance(row, dict) else None
        if action in wanted:
            new = dict(wanted[action])
            if keep_added_at and row.get("added_at"):
                new["added_at"] = row["added_at"]
            merged.append(new)
            seen.add(action)
        else:
            merged.append(row)
    for action, row in wanted.items():
        if action not in seen:
            merged.append(row)
    return merged


def _diff(existing: list | None, ours: list[dict]) -> list[str]:
    current = {r.get("action"): r for r in (existing or []) if isinstance(r, dict)}
    lines = []
    for row in ours:
        have = current.get(row["action"])
        if have is None:
            lines.append(f"add {row['action']}")
            continue
        probe = dict(have)
        probe.pop("added_at", None)
        if probe != row:
            changed = sorted(
                k for k in set(probe) | set(row) if probe.get(k) != row.get(k)
            )
            lines.append(f"update {row['action']} ({', '.join(changed)})")
    return lines


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    from sqlalchemy.orm.attributes import flag_modified

    from app.db.config_models import ConnectionSpec
    from app.db.engine import _ConfigSessionLocal
    from app.services import config_validator

    db = _ConfigSessionLocal()
    try:
        spec = (
            db.query(ConnectionSpec)
            .filter(ConnectionSpec.connector_name == CONNECTOR)
            .first()
        )
        if spec is None:
            raise SystemExit(f"no '{CONNECTOR}' connector spec in the config DB")
        if not isinstance(spec.endpoints, list):
            raise SystemExit(
                f"'{CONNECTOR}' has not been split into tools and endpoints — "
                "run scripts/sync_split_connector_endpoints.py first"
            )
        ours = tool_rows()
        changes = _diff(spec.endpoints, ENDPOINTS) + _diff(spec.tools, ours)
        if not changes:
            print("Already in sync.")
            return 0
        spec.endpoints = _merge(spec.endpoints, ENDPOINTS, keep_added_at=False)
        spec.tools = _merge(spec.tools, ours, keep_added_at=True)
        spec.version = (spec.version or 0) + 1
        flag_modified(spec, "endpoints")
        flag_modified(spec, "tools")
        db.flush()

        report = config_validator.validate_config(db=None, config_db=db)
        mine = {f"{CONNECTOR}.{r['action']}" for r in ENDPOINTS + ours}
        issues = report.get("issues") or []
        own_issues = [i for i in issues if any(m in json.dumps(i) for m in mine)]
        # The catalog seed (the NEXT ship step) is what claims finance_weekly
        # for the Norm Finance App — and it refuses that App until
        # get_weekly_financials exists, i.e. until this script has run. So a
        # "no app composition declares it" issue for a component
        # finance_core/catalog.py declares is expected here, not a fault.
        from app.services.finance_core.catalog import APPS as _FIN_APPS

        _declared = {c["key"] for a in _FIN_APPS.values() for c in a["components"]}
        own_issues = [
            i
            for i in own_issues
            if not (
                "no app composition declares it" in json.dumps(i)
                and any(f"'{k}'" in json.dumps(i) for k in _declared)
            )
        ]

        verb = "Would apply" if args.dry_run else "Applied"
        for line in changes:
            print(f"{verb}: {line}")
        print(
            f"validate_config: {'ok' if report.get('ok') else str(len(issues)) + ' issue(s)'}"
            f" — {len(own_issues)} mentioning these rows"
        )
        for issue in own_issues:
            print("  -", issue)
        if own_issues:
            db.rollback()
            print("nothing applied: fix the issues above first")
            return 1
        if args.dry_run:
            db.rollback()
            print(
                f"(dry run — nothing written; spec would move to version {spec.version})"
            )
            return 0
        db.commit()
        print(f"committed; {CONNECTOR} spec version -> {spec.version}")
        return 0
    finally:
        db.close()


if __name__ == "__main__":
    raise SystemExit(main())
