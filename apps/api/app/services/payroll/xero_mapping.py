"""The two payroll mapping blocks on a venue's Xero connection (plan §6.1,
xero-api.md §6).

``Connection.config.xero_mapping`` is where ``create_bill`` already keeps the
venue's account map, so the payroll journal's line-type → account map and
the intercompany block (the recharge accounts and the counterparty → Xero
contact map) live beside it:

    xero_mapping.payroll      = {wages_payable, payroll_taxes_payable, …, rounding}
    xero_mapping.intercompany = {wages_account, leave_account, contacts: {<venue id>: {contact_id, name}}}
    xero_mapping.account_names = {<code>: <name>}   # cached from the chart at save time

**Validated by account class** against the tenant's chart (``xero.get_accounts``):
Orbit let CBI map its intercompany wages to an expense account, and every
invoice to a CBI counterparty was then rejected by Xero ("OUTPUT2 cannot be
used with 52100") before a retry without a tax type made it land by accident
— 67 logged errors (report 03 §13 item 2). A missing key is **never
defaulted**: it is a named blocker on the reconciliation, and the journal
build refuses.
"""

from __future__ import annotations

from sqlalchemy.orm import Session
from sqlalchemy.orm.attributes import flag_modified

from app.db.models import Connection, Venue
from app.services.finance_core.ctx import FinanceCtx, require_venue
from app.services.finance_core.errors import Invalid

CONNECTOR = "xero"

#: Every journal line type, in the order the journal lists them.
PAYROLL_KEYS = (
    "wages_payable",
    "payroll_taxes_payable",
    "unpaid_wages",
    "security_owed",
    "intercompany_wages",
    "bar_floor",
    "kitchen",
    "other_team",
    "salaries",
    "site_manager_bonus",
    "leave_entitlements",
    "entertainers",
    "entertainment_expense",
    "gratuities_reclass",
    "rounding",
)
INTERCOMPANY_KEYS = ("wages_account", "leave_account")

#: Keys every journal needs whatever the week holds (the rest only when
#: their line is non-zero — journal.py names them as it builds).
ALWAYS_NEEDED = (
    "wages_payable",
    "payroll_taxes_payable",
    "bar_floor",
    "kitchen",
    "leave_entitlements",
)

#: Accepted Xero account classes per key (plan §6.1; report 03 §8.2, §13.2).
#: ``warn`` classes are accepted with a warning; anything else is an error.
_RULES: dict[str, dict[str, tuple[str, ...]]] = {
    "wages_payable": {"ok": ("LIABILITY",)},
    "payroll_taxes_payable": {"ok": ("LIABILITY",)},
    "unpaid_wages": {"ok": ("LIABILITY",)},
    "security_owed": {"ok": ("LIABILITY",), "warn": ("EXPENSE",)},
    "intercompany_wages": {"ok": ("LIABILITY", "ASSET")},
    "bar_floor": {"ok": ("EXPENSE",)},
    "kitchen": {"ok": ("EXPENSE",)},
    "other_team": {"ok": ("EXPENSE",)},
    "salaries": {"ok": ("EXPENSE",)},
    "site_manager_bonus": {"ok": ("EXPENSE",)},
    "leave_entitlements": {"ok": ("EXPENSE",)},
    "entertainers": {"ok": ("EXPENSE",)},
    "entertainment_expense": {"ok": ("EXPENSE",)},
    "gratuities_reclass": {"ok": ("REVENUE", "EXPENSE")},
    "rounding": {"ok": ("EXPENSE",)},
    "intercompany.wages_account": {"ok": ("LIABILITY", "ASSET")},
    "intercompany.leave_account": {"ok": ("LIABILITY", "ASSET", "EXPENSE", "REVENUE")},
}
_WHY_NOT_EXPENSE = (
    "an expense account cannot take the sales tax type on an invoice to a "
    "counterparty (CBI's 52100 defect) — use a liability or asset clearing account"
)


def connection(db: Session, venue_id: str | None) -> Connection | None:
    """The venue's own live Xero connection row, else None (never another
    venue's — the books must be this entity's)."""
    if not venue_id:
        return None
    row = (
        db.query(Connection)
        .filter(Connection.connector_name == CONNECTOR, Connection.venue_id == venue_id)
        .first()
    )
    if not row or row.enabled != "true" or not row.access_token or row.needs_reconnect:
        return None
    return row


def mapping_of(db: Session, venue_id: str) -> dict:
    """The whole ``xero_mapping`` block (``{}`` when not connected)."""
    row = connection(db, venue_id)
    return dict((row.config or {}).get("xero_mapping") or {}) if row else {}


def _code(value) -> str:
    """A mapping value's account code. Two shapes are stored: the importer
    (F7) writes ``{"code": "814", "name": "Wages Payable - Payroll"}``; a
    bare code string is accepted too."""
    if isinstance(value, dict):
        value = value.get("code")
    return str(value or "").strip()


def _name(value) -> str | None:
    if isinstance(value, dict) and value.get("name"):
        return str(value["name"]).strip() or None
    return None


def payroll_of(db: Session, venue_id: str) -> dict[str, str]:
    """``{key: code}`` for the payroll block, whichever shape is stored."""
    out = {}
    for k, v in (mapping_of(db, venue_id).get("payroll") or {}).items():
        code = _code(v)
        if code:
            out[k] = code
    return out


def intercompany_of(db: Session, venue_id: str) -> dict:
    """The intercompany block with its two accounts as bare codes."""
    block = dict(mapping_of(db, venue_id).get("intercompany") or {})
    for key in INTERCOMPANY_KEYS:
        if key in block:
            block[key] = _code(block[key]) or None
    block.setdefault("contacts", {})
    return block


def contacts_of(db: Session, venue_id: str) -> dict[str, dict]:
    """``{counterparty venue id: {contact_id, name}}``."""
    out = {}
    for vid, c in (intercompany_of(db, venue_id).get("contacts") or {}).items():
        if isinstance(c, dict) and c.get("contact_id"):
            out[str(vid)] = {"contact_id": str(c["contact_id"]), "name": c.get("name")}
    return out


def account_names_of(db: Session, venue_id: str) -> dict[str, str]:
    """``{code: name}`` — the names cached at save time plus the ones the
    importer wrote on each ``{code, name}`` entry."""
    block = mapping_of(db, venue_id)
    out = {str(k): str(v) for k, v in (block.get("account_names") or {}).items() if v}
    for section in ("payroll", "intercompany"):
        for v in (block.get(section) or {}).values():
            if _code(v) and _name(v):
                out.setdefault(_code(v), _name(v))
    return out


def missing_for_journal(payroll: dict, needed) -> list[str]:
    """The keys a journal needs that the mapping lacks, in line order."""
    wanted = set(ALWAYS_NEEDED) | set(needed)
    return [k for k in PAYROLL_KEYS if k in wanted and not payroll.get(k)]


# ---------------------------------------------------------------------------
# Reading the chart
# ---------------------------------------------------------------------------


def chart(db: Session, config_db: Session, venue_id: str) -> list[dict]:
    """The tenant's active chart with class and type — ``xero.get_accounts``
    through the shared executor, with this venue's token only."""
    from app.connectors.tool_executor import execute_connector_tool

    result = execute_connector_tool(
        connector_name=CONNECTOR,
        action="get_accounts",
        params={"venue_id": venue_id, "kind": "all", "limit": "5000"},
        db=db,
        config_db=config_db,
        venue_id=venue_id,
        strict_venue=True,
    )
    payload = result.payload if isinstance(result.payload, dict) else {}
    if not result.success or payload.get("error"):
        raise Invalid(
            "Xero's chart of accounts is unavailable: "
            + str(payload.get("error") or result.error or "no answer")
        )
    return [a for a in (payload.get("accounts") or []) if isinstance(a, dict)]


def _by_code(accounts: list[dict]) -> dict[str, dict]:
    return {str(a.get("code")): a for a in accounts if a.get("code")}


# ---------------------------------------------------------------------------
# Validation and saving
# ---------------------------------------------------------------------------


def _clean(block, keys) -> dict[str, str]:
    if block is None:
        return {}
    if not isinstance(block, dict):
        raise Invalid("A mapping block is an object of key → account code.")
    out = {}
    for k in keys:
        code = _code(block.get(k))
        if code:
            out[k] = code
    unknown = sorted(set(block) - set(keys) - {"contacts"})
    if unknown:
        raise Invalid(f"Unknown mapping key(s): {', '.join(unknown)}.")
    return out


def _issue(level: str, key: str, message: str) -> dict:
    return {"level": level, "key": key, "message": message}


def validate(
    db: Session,
    ctx: FinanceCtx,
    venue_id: str,
    payroll: dict | None,
    intercompany: dict | None,
    *,
    accounts: list[dict],
) -> tuple[dict, dict, list[dict]]:
    """Clean both blocks and check every code against the chart by class.
    Returns (payroll, intercompany, issues); an ``error`` issue refuses a
    save."""
    pay = _clean(payroll, PAYROLL_KEYS)
    ico_in = intercompany if isinstance(intercompany, dict) else {}
    ico = _clean(
        {k: v for k, v in ico_in.items() if k != "contacts"}, INTERCOMPANY_KEYS
    )
    codes = _by_code(accounts)
    issues: list[dict] = []

    def check(key: str, code: str, rule_key: str) -> None:
        acct = codes.get(code)
        if acct is None:
            issues.append(
                _issue("error", key, f"{code} is not an active account in Xero")
            )
            return
        klass = str(acct.get("class") or "").upper()
        rule = _RULES[rule_key]
        if klass in rule["ok"]:
            return
        if klass in rule.get("warn", ()):
            issues.append(
                _issue(
                    "warning",
                    key,
                    f"{code} {acct.get('name') or ''} is a {klass.lower()} account "
                    f"(expected {' or '.join(c.lower() for c in rule['ok'])})",
                )
            )
            return
        why = (
            _WHY_NOT_EXPENSE
            if rule_key in ("intercompany_wages", "intercompany.wages_account")
            and klass == "EXPENSE"
            else f"expected {' or '.join(c.lower() for c in rule['ok'])}"
        )
        issues.append(
            _issue(
                "error",
                key,
                f"{code} {acct.get('name') or ''} is a {klass.lower() or 'typeless'} account — {why}",
            )
        )

    for key, code in pay.items():
        check(key, code, key)
    for key, code in ico.items():
        check(f"intercompany.{key}", code, f"intercompany.{key}")

    contacts_in = ico_in.get("contacts") or {}
    if not isinstance(contacts_in, dict):
        raise Invalid(
            "intercompany.contacts is an object of venue id → {contact_id, name}."
        )
    org_venues = {
        v.id: v.name
        for v in db.query(Venue).filter(Venue.organization_id == ctx.org_id).all()
    }
    contacts = {}
    for vid, c in contacts_in.items():
        vid = str(vid)
        if vid not in org_venues:
            issues.append(
                _issue(
                    "error",
                    f"intercompany.contacts.{vid}",
                    "not a venue of this organisation",
                )
            )
            continue
        if vid == venue_id:
            issues.append(
                _issue(
                    "error",
                    f"intercompany.contacts.{vid}",
                    "a venue is not its own counterparty",
                )
            )
            continue
        if not isinstance(c, dict) or not str(c.get("contact_id") or "").strip():
            # An empty entry clears the contact.
            continue
        contacts[vid] = {
            "contact_id": str(c["contact_id"]).strip(),
            "name": str(c.get("name") or "").strip() or None,
        }
    ico["contacts"] = contacts
    return pay, ico, issues


def read(
    db: Session, ctx: FinanceCtx, venue_id: str, *, config_db: Session | None = None
) -> dict:
    """What the settings panel shows (xero-api.md §6)."""
    ctx.require("payroll:read")
    require_venue(ctx, venue_id)
    row = connection(db, venue_id)
    block = dict((row.config or {}).get("xero_mapping") or {}) if row else {}
    payroll = {k: _code(v) for k, v in (block.get("payroll") or {}).items() if _code(v)}
    accounts: list[dict] = []
    chart_error = None
    if row is not None and config_db is not None:
        try:
            accounts = chart(db, config_db, venue_id)
        except Invalid as exc:
            chart_error = exc.message
    return {
        "venue_id": venue_id,
        "connected": row is not None,
        "tenant": (row.config or {}).get("xero_tenant_id") if row else None,
        "organisation": (
            (row.oauth_metadata or {}).get("tenant_name") if row else None
        ),
        "payroll": payroll,
        "intercompany": {
            **{
                k: v
                for k, v in (block.get("intercompany") or {}).items()
                if k != "contacts" and v
            },
            "contacts": contacts_of(db, venue_id) if row else {},
        },
        "tracking": block.get("tracking") or {},
        "account_names": block.get("account_names") or {},
        "accounts": [
            {
                "code": a.get("code"),
                "name": a.get("name"),
                "type": a.get("type"),
                "class": a.get("class"),
            }
            for a in accounts
        ],
        "chart_error": chart_error,
        "missing_for_journal": missing_for_journal(payroll, ()),
        "keys": {
            "payroll": list(PAYROLL_KEYS),
            "intercompany": list(INTERCOMPANY_KEYS),
        },
    }


def save(
    db: Session,
    ctx: FinanceCtx,
    venue_id: str,
    payroll: dict | None,
    intercompany: dict | None,
    *,
    config_db: Session,
    validate_only: bool = False,
) -> dict:
    """Validate, then write both blocks (and the account-name cache) onto the
    venue's Xero connection. Flushes; the route commits. Returns the issues
    and what was saved."""
    ctx.require("payroll:write")
    require_venue(ctx, venue_id)
    row = connection(db, venue_id)
    if row is None:
        raise Invalid(
            "This venue is not connected to Xero — connect it under Settings → Connections first."
        )
    accounts = chart(db, config_db, venue_id)
    pay, ico, issues = validate(
        db, ctx, venue_id, payroll, intercompany, accounts=accounts
    )
    errors = [i for i in issues if i["level"] == "error"]
    saved = False
    if not errors and not validate_only:
        codes = _by_code(accounts)
        used = set(pay.values()) | {v for k, v in ico.items() if k != "contacts"}
        config = dict(row.config or {})
        block = dict(config.get("xero_mapping") or {})
        # The importer's shape (F7): {code, name} per payroll key.
        block["payroll"] = {
            k: {"code": code, "name": str(codes[code].get("name") or "")}
            for k, code in pay.items()
        }
        block["intercompany"] = ico
        block["account_names"] = {
            code: str(codes[code].get("name") or "")
            for code in sorted(used)
            if code in codes
        }
        config["xero_mapping"] = block
        row.config = config
        flag_modified(row, "config")
        db.flush()
        saved = True
    return {
        "venue_id": venue_id,
        "saved": saved,
        "issues": issues,
        "payroll": pay,
        "intercompany": ico,
        "missing_for_journal": missing_for_journal(pay, ()),
    }
