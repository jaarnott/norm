"""The two engine-only built-ins the Xero push tools call (connector
``norm_payroll``; plan §10, DESIGN-CONTRACT §4/§5).

Neither is ever shown to a model. ``xero.post_wage_journal``,
``xero.post_intercompany_documents`` and ``xero.void_payroll_document`` (the
consolidators) read the stored draft through ``get_journal_draft`` and
record what they put in Xero through ``record_push``; the ledger service
(``services/payroll/xero_pushes``) is what the entertainers module calls
directly for its bills.

Both refuse, whatever door the call came through, unless **a person** is
acting who holds ``payroll:write`` in the venue's organisation, the module is
``live``, and the run is attended. The person is the thread's user, else the
caller scope's — never a parameter, so a model cannot choose who it acts as.
An unattended (task-run) thread gets ``{"error": "unattended run"}``, the
rule F3's ``norm_payroll.get_mode`` applies (``_unattended`` below is the
same four lines; build-notes asks for one helper in ``finance_core``).
"""

from __future__ import annotations

import json

from sqlalchemy.orm import Session

from app.agents.internal_tools import _user_for_thread, register
from app.db.models import User
from app.services import caller_scope
from app.services.finance_core.ctx import FinanceCtx, build_ctx, require_venue
from app.services.finance_core.errors import FinanceError
from app.services.payroll import journal as journal_svc
from app.services.payroll import reconciliation as recon_svc
from app.services.payroll import xero_pushes

CONNECTOR = "norm_payroll"
UNATTENDED = "unattended run"


class Refused(Exception):
    """A refusal the caller reads as ``{"error": ...}``."""


def _unattended(db: Session, thread_id: str | None) -> bool:
    """A scheduled task's run (its thread is ``<member>.automated_task``)."""
    from app.db.models import Thread

    if not thread_id:
        return False
    intent = db.query(Thread.intent).filter(Thread.id == thread_id).scalar()
    return bool(intent) and intent.endswith(".automated_task")


def _ctx(db: Session, thread_id: str | None) -> FinanceCtx:
    """The person acting, as a Finance caller — refused for nobody, for a
    person without ``payroll:write``, and unless payroll is live."""
    if _unattended(db, thread_id):
        raise Refused(UNATTENDED)
    user = _user_for_thread(thread_id, db)
    org_id = None
    if user is None:
        scope = caller_scope.current()
        if scope is not None and scope.user_id:
            user = db.query(User).filter(User.id == scope.user_id).first()
            org_id = scope.org_id
    if user is None:
        raise Refused(
            "Norm couldn't tell who is asking — a person holding payroll:write "
            "has to run this."
        )
    ctx = build_ctx(db, user, org_id=org_id, actor_label="Norm")
    ctx.require("payroll:write")
    ctx.require_live("payroll")
    return ctx


def _venue(db: Session, ctx: FinanceCtx, params: dict):
    """The venue named by id or whole name — one of the caller's own."""
    from app.db.models import Venue

    venue_id = str(params.get("venue_id") or "").strip()
    name = str(params.get("venue") or params.get("venue_name") or "").strip()
    venue = None
    if venue_id:
        venue = (
            db.query(Venue)
            .filter(Venue.organization_id == ctx.org_id, Venue.id == venue_id)
            .first()
        )
    elif name:
        with caller_scope.use(caller_scope.Scope(ctx.org_id, user_id=ctx.user_id)):
            venue, _ = caller_scope.find_venue(db, name=name)
    if venue is None:
        raise Refused("name the venue (a venue of your organisation)")
    require_venue(ctx, venue.id)
    return venue


def _tool(action: str):
    """Register a handler whose refusals come back as the error the
    consolidator reads."""

    def decorator(fn):
        def handler(params: dict, db: Session, thread_id: str | None) -> dict:
            try:
                data = fn(dict(params or {}), db, thread_id)
            except FinanceError as exc:
                return {"success": False, "data": {}, "error": exc.message}
            except Refused as exc:
                return {"success": False, "data": {}, "error": str(exc)}
            return {"success": True, "data": data, "error": None}

        handler.__name__ = fn.__name__
        handler.__doc__ = fn.__doc__
        register(CONNECTOR, action)(handler)
        return handler

    return decorator


def _obj(value):
    """A dict or list a consolidator passed, or JSON text of one."""
    if isinstance(value, str):
        try:
            return json.loads(value)
        except ValueError:
            return None
    return value


# ---------------------------------------------------------------------------
# get_journal_draft
# ---------------------------------------------------------------------------


@_tool("get_journal_draft")
def _get_journal_draft(params: dict, db: Session, thread_id: str | None) -> dict:
    """The stored journal draft (``kind=journal``), the ICO drafts
    (``kind=ico``) or one push-ledger row (``push_id``) for the Xero tools —
    refused when the hash the caller saw is not the draft's, or the inputs
    moved since the draft was built."""
    ctx = _ctx(db, thread_id)
    push_id = str(params.get("push_id") or "").strip()
    if push_id:
        push = xero_pushes.get_push(db, ctx, push_id)
        draft = None
        if push.draft_id:
            try:
                draft = journal_svc.draft_dict(
                    journal_svc.get_draft(db, ctx, push.draft_id)
                )
            except FinanceError:
                draft = None
        return {
            "organization_id": ctx.org_id,
            "push": xero_pushes.to_dict(push),
            "draft": draft,
        }

    venue = _venue(db, ctx, params)
    week_start = recon_svc.week_start_of(
        params.get("week_start"), params.get("week_ending")
    )
    kind = str(params.get("kind") or "journal").strip().lower()
    current = journal_svc.current(db, ctx, venue.id, week_start)
    base = {
        "organization_id": ctx.org_id,
        "venue_id": venue.id,
        "venue": venue.name,
        "week_start": current["week_start"],
        "week_ending": current["week_ending"],
        "blockers": current["blockers"],
    }
    if kind == "journal":
        draft = current["journal"]
        if draft is None:
            raise Refused(
                f"no journal draft for {venue.name} WE {current['week_ending']} — "
                "build it on the Reconciliation page first"
            )
        wanted = str(params.get("draft_id") or "").strip()
        if wanted and wanted != draft["id"]:
            raise Refused(
                "the draft you saw has been rebuilt — reload the page and look again"
            )
        seen = str(params.get("basis_sha256") or "").strip()
        if seen and seen != draft["basis_sha256"]:
            raise Refused(
                "the draft's figures changed since you saw them — reload and look again"
            )
        if draft["stale"]:
            raise Refused(
                "the week's inputs changed since the draft was built — rebuild the draft first"
            )
        if current["blockers"]:
            raise Refused(
                "the journal can't be pushed yet: " + "; ".join(current["blockers"])
            )
        return {
            **base,
            "draft": {
                k: v
                for k, v in draft.items()
                if k not in ("journal", "narration", "live_push")
            },
            "journal": draft["journal"],
            "narration": draft["narration"],
            "live_push": draft["live_push"],
            "pushes_before": xero_pushes.count_for_slot(
                db, ctx.org_id, venue_id=venue.id, kind="journal", week_start=week_start
            ),
        }
    if kind == "ico":
        docs = []
        for row in current["ico"]:
            if row["stale"]:
                raise Refused(
                    "the week's inputs changed since the intercompany drafts were built — "
                    "rebuild the draft first"
                )
            docs.append(
                {
                    "draft_id": row["id"],
                    "kind": row["kind"],
                    "counterparty_venue_id": row["counterparty_venue_id"],
                    "counterparty": row["counterparty"],
                    "contact": row["contact"],
                    "lines": row["lines"],
                    "wages_cents": row["wages_cents"],
                    "uplift_bp": row["uplift_bp"],
                    "uplift_cents": row["uplift_cents"],
                    "subtotal_cents": row["subtotal_cents"],
                    "live_push": row["live_push"],
                    "pushes_before": xero_pushes.count_for_slot(
                        db,
                        ctx.org_id,
                        venue_id=venue.id,
                        kind=row["kind"],
                        week_start=week_start,
                        counterparty_venue_id=row["counterparty_venue_id"],
                    ),
                }
            )
        return {
            **base,
            "wages_account": current["intercompany"]["wages_account"],
            "leave_account": current["intercompany"]["leave_account"],
            "uplift_bp": current["intercompany"]["uplift_bp"],
            "documents": docs,
        }
    raise Refused("kind is 'journal' or 'ico'")


# ---------------------------------------------------------------------------
# record_push
# ---------------------------------------------------------------------------

_DOC_KEYS = (
    "kind",
    "venue_id",
    "week_start",
    "week_ending",
    "draft_id",
    "counterparty_venue_id",
    "entertainer_performance_id",
    "xero_id",
    "xero_number",
    "xero_status",
    "idempotency_key",
    "request_payload",
    "response",
)


@_tool("record_push")
def _record_push(params: dict, db: Session, thread_id: str | None) -> dict:
    """Write the push ledger: ``op=record`` one document or a list of them
    (idempotent on the key), ``op=void`` one row. ``pushed_by`` is the
    caller, never a parameter."""
    ctx = _ctx(db, thread_id)
    op = str(params.get("op") or "record").strip().lower()
    if op == "void":
        push_id = str(params.get("push_id") or "").strip()
        if not push_id:
            raise Refused("push_id is required")
        row = xero_pushes.void_push(
            db, ctx, push_id, xero_status=params.get("xero_status") or None
        )
        return {"voided": True, "push": xero_pushes.brief(row)}
    if op != "record":
        raise Refused("op is 'record' or 'void'")
    docs = _obj(params.get("documents"))
    if docs is None:
        docs = [params]
    if isinstance(docs, dict):
        docs = [docs]
    if not isinstance(docs, list) or not docs:
        raise Refused("documents is a list of pushes to record")
    out = []
    for doc in docs:
        if not isinstance(doc, dict):
            raise Refused("each document is an object")
        d = {k: doc.get(k, params.get(k)) for k in _DOC_KEYS}
        venue_id = str(d.get("venue_id") or "").strip()
        if not venue_id:
            venue_id = _venue(db, ctx, params).id
        week_start = None
        if d.get("week_start") or d.get("week_ending"):
            week_start = recon_svc.week_start_of(
                d.get("week_start"), d.get("week_ending")
            )
        row = xero_pushes.record_push(
            db,
            ctx,
            kind=str(d.get("kind") or ""),
            venue_id=venue_id,
            idempotency_key=str(d.get("idempotency_key") or ""),
            xero_id=str(d["xero_id"]) if d.get("xero_id") else None,
            xero_number=str(d["xero_number"]) if d.get("xero_number") else None,
            xero_status=str(d["xero_status"]) if d.get("xero_status") else None,
            week_start=week_start,
            draft_id=str(d["draft_id"]) if d.get("draft_id") else None,
            counterparty_venue_id=str(d["counterparty_venue_id"])
            if d.get("counterparty_venue_id")
            else None,
            entertainer_performance_id=str(d["entertainer_performance_id"])
            if d.get("entertainer_performance_id")
            else None,
            request_payload=_obj(d.get("request_payload")),
            response=_obj(d.get("response")),
        )
        out.append(xero_pushes.brief(row))
    return {
        "recorded": len(out),
        "pushes": out,
        "push_id": out[0]["id"] if len(out) == 1 else None,
    }


# ---------------------------------------------------------------------------
# The rows scripts/sync_norm_finance.py installs
# ---------------------------------------------------------------------------


def _schema(**fields: str) -> dict:
    return {k: {"type": "string", "description": v} for k, v in fields.items()}


TOOL_ROWS: list[dict] = [
    {
        "action": "get_journal_draft",
        "method": "GET",
        "read_only": True,
        "engine_only": True,
        "effect": "read",
        "requires": ["payroll:write"],
        "description": (
            "Engine-only. The stored Xero journal draft (kind=journal), the "
            "intercompany drafts (kind=ico) or one push-ledger row (push_id) for "
            "an entity's pay week — what xero.post_wage_journal, "
            "post_intercompany_documents and void_payroll_document read. Refuses "
            "unless the person holds payroll:write and payroll is live; refuses "
            "an unattended run and a draft whose inputs moved."
        ),
        "required_fields": [],
        "optional_fields": [
            "venue",
            "venue_id",
            "week_ending",
            "week_start",
            "kind",
            "draft_id",
            "basis_sha256",
            "push_id",
        ],
        "field_schema": _schema(
            venue="Venue name (the payroll entity)",
            venue_id="Venue id",
            week_ending="Pay week's Sunday, YYYY-MM-DD (any day of the week is snapped)",
            week_start="Pay week's Monday, YYYY-MM-DD",
            kind="'journal' (default) or 'ico'",
            draft_id="The draft the caller saw; refused if it was rebuilt",
            basis_sha256="The draft's basis hash the caller saw; refused if it differs",
            push_id="A push-ledger row id (returns the push and its draft instead)",
        ),
        "max_result_chars": 60_000,
    },
    {
        "action": "record_push",
        "method": "POST",
        "read_only": False,
        "engine_only": True,
        "effect": "write",
        "requires": ["payroll:write"],
        "description": (
            "Engine-only. Writes the Xero push ledger (pay_xero_pushes) after a "
            "document landed in Xero: op=record one document or a list "
            "(idempotent on the idempotency key — the same key returns the row "
            "already written; a second live document for the same week and kind "
            "is refused), op=void marks a row voided. pushed_by is the caller."
        ),
        "required_fields": [],
        "optional_fields": [
            "op",
            "documents",
            "push_id",
            "xero_status",
            *_DOC_KEYS[1:],
        ],
        "field_schema": {
            **_schema(
                op="'record' (default) or 'void'",
                push_id="op=void: the ledger row to mark voided",
                xero_status="The document's Xero status (DRAFT, POSTED, VOIDED, DELETED, AUTHORISED…)",
                kind="journal | ico_invoice | ico_bill | entertainer_bill",
                venue_id="The entity's venue id",
                week_start="Pay week's Monday, YYYY-MM-DD",
                week_ending="Pay week's Sunday, YYYY-MM-DD",
                draft_id="The pay_journal_drafts row pushed",
                counterparty_venue_id="ICO documents: the counterparty venue id",
                entertainer_performance_id="Entertainer bills: the performance id",
                xero_id="Xero ManualJournalID / InvoiceID",
                xero_number="Xero's number for an invoice or bill",
                idempotency_key="The Idempotency-Key sent to Xero",
            ),
            "documents": {
                "type": "array",
                "description": "op=record: several documents at once, each with the fields above",
                "items": {"type": "object"},
            },
            "request_payload": {
                "type": "object",
                "description": "What was sent to Xero",
            },
            "response": {
                "type": "object",
                "description": "What Xero answered (trimmed)",
            },
        },
        "max_result_chars": 20_000,
    },
]
