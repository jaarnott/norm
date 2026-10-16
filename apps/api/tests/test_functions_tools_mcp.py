"""Norm Functions over Norm's MCP (the claude.ai "Norm" connector).

Norm's MCP policy, followed as it is: direct tools are reads only, and
drafting, record changes and sends go through the skills as workflow tools
that run Norm's own agent as the token's user — drafts land in Norm, and a
write or a customer send waits on Norm's approval card.

* Identity: a direct tool runs as the token's user (carried on the caller
  scope by the MCP entry point — never a param), in the token's organisation,
  only at the venues they consented to share. No user → refused.
* Isolation: user A in organisation A reaches only A's records; an
  organisation with Functions off sees no Functions tools or workflows.
* Approvals: no draft or write is ever a direct tool, even if a capability
  row names one; inside a workflow run they behave as in chat — drafts are
  made, writes and sends suspend for approval in Norm.
"""

from __future__ import annotations

import importlib.util
import json
import pathlib
import sys
import uuid
from contextlib import contextmanager

import pytest

from app.agents import functions_tools as FT
from app.agents import internal_tools as IT
from app.db.config_models import MarketplaceApp, McpCapability
from app.mcp.execution import NormMcpContext
from app.mcp.principal import McpPrincipal
from app.services import agent_catalog, caller_scope
from app.services.functions import catalog
from tests.functions_world import add_contact, add_enquiry, make_world

API = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(API))


def _script():
    spec = importlib.util.spec_from_file_location(
        "sync_norm_functions", API / "scripts" / "sync_norm_functions.py"
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


SYNC = _script()
READ = "mcp:functions:read"
DRAFT = "mcp:functions:draft"


@pytest.fixture(autouse=True)
def _config(monkeypatch, db_session):
    @contextmanager
    def same_session():
        yield db_session

    monkeypatch.setattr(FT, "_config_db", same_session)
    agent_catalog.invalidate_cache()
    yield
    agent_catalog.invalidate_cache()


def _seed(db):
    """Ship day on the test session: the catalog (any member row arms team
    gating), then the sync script's spec, skills and MCP capabilities."""
    db.add(
        MarketplaceApp(
            slug="procurement-agent",
            name="Procurement",
            description="",
            tier="agent",
            bundled=True,
            composition={"owns_agents": ["procurement"]},
        )
    )
    for row in catalog.stand_in_rows(catalog.CATALOG_SLUGS):
        db.add(row)
    db.flush()
    SYNC._spec(db, dry_run=False)
    SYNC._skills(db, dry_run=False)
    SYNC._mcp(db, dry_run=False)
    db.flush()


def _principal(user, org, venues, scopes=(READ,)):
    return McpPrincipal(
        user_id=user.id,
        organization_id=org.id,
        venue_ids=tuple(v.id for v in venues),
        scopes=frozenset(scopes),
    )


def _mcp(db, principal):
    return NormMcpContext(principal=principal, db=db, config_db=db)


def _body(result) -> dict:
    return json.loads(result["content"][0]["text"])


@pytest.fixture()
def w(db_session):
    w = make_world(db_session)
    _seed(db_session)
    mine = add_contact(db_session, w.org.id, email="casey@clientco.nz")
    w.mine = add_enquiry(
        db_session, w.org.id, mine.id, w.venue.id, subject="Casey's 40th"
    )
    garden = add_contact(db_session, w.org.id, email="gus@garden.nz")
    w.garden = add_enquiry(
        db_session, w.org.id, garden.id, w.venue2.id, subject="Garden party"
    )
    rival = add_contact(db_session, w.other_org.id, email="rita@rival.nz")
    w.theirs = add_enquiry(
        db_session, w.other_org.id, rival.id, w.other_venue.id, subject="Rival booking"
    )
    return w


class TestTheSurface:
    def test_reads_are_direct_tools_and_drafts_and_writes_are_not(self, db_session, w):
        names = {
            t["name"]
            for t in _mcp(
                db_session, _principal(w.staff, w.org, [w.venue])
            ).list_tools()
        }
        reads = {
            f"norm_functions__{r['action']}"
            for r in SYNC.tool_rows()
            if r["effect"] == "read"
        }
        assert reads <= names
        others = {f"norm_functions__{a}" for a in FT.ACTIONS} - reads
        assert not others & names

    def test_a_capability_naming_a_write_still_never_projects(self, db_session, w):
        db_session.add(
            McpCapability(
                id=str(uuid.uuid4()),
                kind="connector",
                target="norm_functions",
                action="send_email",
                scopes=[READ],
                enabled=True,
            )
        )
        db_session.flush()
        names = {
            t["name"]
            for t in _mcp(
                db_session, _principal(w.staff, w.org, [w.venue])
            ).list_tools()
        }
        assert "norm_functions__send_email" not in names

    def test_workflows_need_the_draft_scope_and_leave_out_email_tone(
        self, db_session, w
    ):
        names = {
            t["name"]
            for t in _mcp(
                db_session, _principal(w.staff, w.org, [w.venue], (READ, DRAFT))
            ).list_tools()
        }
        assert {f"norm_playbook__{s}" for s in SYNC.MCP_WORKFLOWS} <= names
        assert "norm_playbook__functions_email_tone" not in names
        read_only = {
            t["name"]
            for t in _mcp(
                db_session, _principal(w.staff, w.org, [w.venue])
            ).list_tools()
        }
        assert not {n for n in read_only if n.startswith("norm_playbook__functions_")}

    def test_an_organisation_with_functions_off_sees_none_of_it(self, db_session, w):
        w.other_org.functions_mode = "off"
        db_session.flush()
        names = {
            t["name"]
            for t in _mcp(
                db_session,
                _principal(w.other_user, w.other_org, [w.other_venue], (READ, DRAFT)),
            ).list_tools()
        }
        assert not {
            n
            for n in names
            if n.startswith(("norm_functions__", "norm_playbook__functions_"))
        }


class TestIdentity:
    def test_the_tokens_user_sees_only_their_organisation(self, db_session, w):
        mcp = _mcp(db_session, _principal(w.staff, w.org, [w.venue]))
        found = _body(mcp.call_tool("norm_functions__find_enquiries", {}))
        assert [r["id"] for r in found["enquiries"]] == [w.mine.id]
        theirs = mcp.call_tool(
            "norm_functions__get_enquiry", {"enquiry_id": w.theirs.id}
        )
        assert theirs["isError"] and "not found" in _body(theirs)["error"].lower()
        mine = _body(
            mcp.call_tool("norm_functions__get_enquiry", {"enquiry_id": w.mine.id})
        )
        assert mine["id"] == w.mine.id

        rival = _mcp(db_session, _principal(w.other_user, w.other_org, [w.other_venue]))
        out = rival.call_tool("norm_functions__get_enquiry", {"enquiry_id": w.mine.id})
        assert out["isError"] and "not found" in _body(out)["error"].lower()
        # A param can't pick another person.
        out = rival.call_tool(
            "norm_functions__get_enquiry",
            {"enquiry_id": w.mine.id, "user_id": w.staff.id},
        )
        assert out["isError"]

    def test_only_the_consented_venues(self, db_session, w):
        """The owner sees every venue in Norm, but shared only Brew Bar."""
        mcp = _mcp(db_session, _principal(w.owner, w.org, [w.venue]))
        found = _body(mcp.call_tool("norm_functions__find_enquiries", {}))
        assert [r["id"] for r in found["enquiries"]] == [w.mine.id]
        out = mcp.call_tool("norm_functions__get_enquiry", {"enquiry_id": w.garden.id})
        assert out["isError"]

    def test_no_user_is_refused(self, db_session, w):
        handler = IT.get_handler("norm_functions", "find_enquiries")
        out = handler({}, db_session, None)
        assert not out["success"] and "No person" in out["error"]
        with caller_scope.use(caller_scope.Scope(w.org.id)):
            out = handler({}, db_session, None)
        assert not out["success"] and "No person" in out["error"]

    def test_a_narrower_scope_keeps_the_person(self, db_session, w):
        outer = caller_scope.Scope(
            w.org.id, user_id=w.staff.id, venue_ids=frozenset({w.venue.id})
        )
        with caller_scope.use(outer):
            with caller_scope.use(caller_scope.Scope(w.org.id)):
                assert caller_scope.current().user_id == w.staff.id
                assert caller_scope.current().venue_ids == frozenset({w.venue.id})
            with caller_scope.use(caller_scope.Scope(w.other_org.id)):
                assert caller_scope.current().user_id is None


class TestWorkflowApprovals:
    """Inside a workflow run (the thread execute_playbook_tool creates), the
    tools behave as in chat: drafts are made, writes and sends wait for
    approval in Norm, and the MCP answer says so."""

    def test_the_run_keeps_every_tool_because_none_acts_unasked(self, db_session, w):
        from app.mcp.workflows import safe_for_claude

        defs = [{"name": f"norm_functions__{a}"} for a in FT.ACTIONS]
        kept = {t["name"] for t in safe_for_claude(defs, db_session)}
        assert kept == {d["name"] for d in defs}

    def test_a_send_in_a_workflow_waits_for_approval_in_norm(
        self, db_session, monkeypatch
    ):
        import datetime as dt

        from app.agents import tool_loop as TL
        from app.db.models import Thread, ToolCall
        from app.mcp.workflows import _map_outcome
        from app.services.functions import enquiries as enquiries_svc
        from app.services.functions.mailbox import sync
        from tests.functions_mailbox_fakes import (
            MAILBOX,
            FakeGmail,
            install,
            mailbox_world,
            sessions,
        )
        from tests.test_tool_loop_unknown_tool import _Block, _Response

        fake = FakeGmail()
        install(monkeypatch, fake)
        mw = mailbox_world(db_session)
        _seed(db_session)
        fake.add_message(
            frm="Casey Client <casey@clientco.nz>",
            to=[MAILBOX],
            subject="Wedding in March",
            body="Can we book the deck?",
            thread_id="t-casey",
            when=dt.datetime.now(dt.timezone.utc) - dt.timedelta(hours=2),
        )
        assert sync.sync_account(
            mw.account.id, session_factory=sessions(db_session)
        ).get("ok")
        db_session.expire_all()
        contact = add_contact(db_session, mw.org.id, email="casey@clientco.nz")
        enquiry = add_enquiry(
            db_session, mw.org.id, contact.id, mw.venue.id, subject="Wedding"
        )
        enquiries_svc.link_thread(
            db_session,
            mw.ctx(),
            account_id=mw.account.id,
            gmail_thread_id="t-casey",
            enquiry_id=enquiry.id,
        )
        run = Thread(
            id=str(uuid.uuid4()),
            user_id=mw.staff.id,
            domain="functions",
            intent="functions.mcp_playbook",
            status="in_progress",
            raw_prompt="x",
            title="[MCP] Proposal build",
            extracted_fields={},
            missing_fields=[],
        )
        db_session.add(run)
        db_session.flush()
        draft = IT.get_handler("norm_functions", "draft_email")(
            {"enquiry_id": enquiry.id, "body": "<p>Yes, the deck is free.</p>"},
            db_session,
            run.id,
        )
        assert draft["success"], draft["error"]  # a workflow run may draft

        calls = []

        def llm(system_prompt, messages, tools, db, thread_id, call_type):
            calls.append(1)
            if len(calls) == 1:
                return _Response(
                    "tool_use",
                    [
                        _Block(
                            "tool_use",
                            name="norm_functions__send_email",
                            id=f"toolu_{uuid.uuid4().hex[:8]}",
                            input={"draft_id": draft["data"]["draft_id"]},
                        )
                    ],
                ), None
            return _Response("end_turn", [_Block("text", text="Done.")]), None

        monkeypatch.setattr("app.interpreter.llm_interpreter.call_llm_with_tools", llm)
        tools = [
            {
                "name": "norm_functions__send_email",
                "description": "[POST] x",
                "input_schema": {"type": "object", "properties": {}},
            }
        ]
        result = TL.run_tool_loop(
            "send it", run, db_session, "sys", tools, config_db=db_session
        )
        db_session.refresh(run)
        assert run.status == "awaiting_tool_approval" and fake.sent == []
        (tc,) = db_session.query(ToolCall).filter(ToolCall.thread_id == run.id).all()
        assert tc.status == "pending_approval" and tc.preview["title"]
        outcome = _map_outcome(db_session, run, result)
        assert outcome["status"] == "pending_approval"
        assert "approval in Norm" in outcome["note"]


class TestConsentAndOwnerPowers:
    def _ctx_under(self, db, user, org, venue_ids):
        scope = caller_scope.Scope(
            org.id, user_id=user.id, venue_ids=frozenset(venue_ids)
        )
        with caller_scope.use(scope):
            return FT._ctx(db, None)

    def test_a_partial_share_drops_owner_powers(self, db_session, w):
        ctx = self._ctx_under(db_session, w.owner, w.org, [w.venue.id])
        assert ctx.venue_ids == frozenset({w.venue.id}) and ctx.is_owner is False

    def test_sharing_the_whole_organisation_keeps_them(self, db_session, w):
        ctx = self._ctx_under(db_session, w.owner, w.org, [w.venue.id, w.venue2.id])
        assert ctx.is_owner is True

    def test_contacts_follow_the_share_over_mcp(self, db_session, w):
        """An owner sees every contact in Norm; shared Brew Bar only, Garden
        Bar's customer is out of reach."""
        from app.services.functions import contacts as contacts_svc
        from app.services.functions.errors import NotFound

        ctx = self._ctx_under(db_session, w.owner, w.org, [w.venue.id])
        with pytest.raises(NotFound):
            contacts_svc.get_contact(db_session, ctx, w.garden.contact_id)
        assert contacts_svc.get_contact(db_session, ctx, w.mine.contact_id)
