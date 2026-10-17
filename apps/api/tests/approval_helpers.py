"""Shared set-up for the approval tests.

Until Oct 2026 seven approval files borrowed these from each other (and
defined a one-row connector three times). Used by test_approval_decisions,
test_approval_labels, test_approval_previews, test_approval_preferences and
test_unattended_runs. The scripted model is tests/tool_loop_harness.py.
"""

import uuid

from app.agents import tool_loop as TL
from app.auth.security import create_access_token
from app.db.config_models import ConnectionSpec
from app.db.models import Message, ToolCall
from app.services import approvals
from tests.conftest import _make_thread
from tests.tool_loop_harness import (
    ScriptedLlm,
    end_turn,
    loop_thread,
    tool,
    tool_round,
    tool_use,
)


def headers(user):
    return {"Authorization": f"Bearer {create_access_token({'sub': user.id})}"}


def owner_thread(db, owner, intent="norm.tool_use"):
    return _make_thread(db, owner, domain="norm", intent=intent, status="in_progress")


def suspend(db, thread, calls, done_results=None):
    """Put ``thread`` in the state the loop leaves it in when it pauses:
    one pending ``loadedhub__<action>`` write per ``(action, params)``, the
    loop state, and the approval card. Returns the tool-call ids."""
    ids = []
    for action, params in calls:
        tc = ToolCall(
            id=str(uuid.uuid4()),
            thread_id=thread.id,
            iteration=1,
            tool_name=f"loadedhub__{action}",
            connector_name="loadedhub",
            action=action,
            method="PUT",
            status="pending_approval",
            input_params=params,
        )
        db.add(tc)
        ids.append(tc.id)
    thread.status = approvals.AWAITING
    thread.pending_tool_call_ids = ids
    thread.agent_loop_state = {
        "messages": [],
        "iteration": 1,
        "done_results": done_results or [],
    }
    db.add(
        Message(
            thread_id=thread.id,
            role="assistant",
            content="I'd like to:\n\n- Manage Stock Item via loadedhub",
            display_blocks=[
                {
                    "component": "tool_approval",
                    "data": {
                        "thread_id": thread.id,
                        "status": "pending",
                        "tool_calls": [{"id": i} for i in ids],
                    },
                    "props": {},
                }
            ],
        )
    )
    db.flush()
    return ids


def spec(db, row: dict) -> str:
    """A connector holding one tool row; returns the connector name."""
    name = f"t{uuid.uuid4().hex[:10]}"
    db.add(
        ConnectionSpec(
            connector_name=name,
            display_name=name,
            execution_mode="internal",
            auth_type="none",
            tools=[row],
        )
    )
    db.flush()
    return name


def ask_turn(
    db,
    user,
    monkeypatch,
    connector,
    action,
    inputs,
    said="Fixing the price.",
    test_mode=False,
):
    """One model turn that calls ``connector__action`` once per entry of
    ``inputs`` (saying ``said`` first), then a turn that ends. Returns the
    thread and the last message each model call was sent."""
    llm = ScriptedLlm(
        [
            tool_round(
                *[
                    tool_use(
                        f"toolu_{uuid.uuid4().hex[:8]}", f"{connector}__{action}", i
                    )
                    for i in inputs
                ],
                text=said,
            ),
            end_turn("Done."),
        ]
    ).install(monkeypatch)
    thread = loop_thread(db, user, domain="norm", status="in_progress")
    TL.run_tool_loop(
        "go",
        thread,
        db,
        "sys",
        [tool(f"{connector}__{action}", description="test")],
        config_db=db,
        test_mode=test_mode,
    )
    return thread, [call[-1] for call in llm.calls]


def price_previewer(before):
    """A built-in previewer for a price change from ``before``."""

    def previewer(params, db, thread_id):
        return {
            "preview": {
                "title": "Change a price",
                "target": params.get("dish", "Bavette"),
                "changes": [
                    {"field": "Price", "before": before, "after": params.get("price")}
                ],
            }
        }

    return previewer


def card_of(db, thread):
    """The approval card block in ``thread``."""
    for msg in db.query(Message).filter(Message.thread_id == thread.id).all():
        for block in msg.display_blocks or []:
            if block.get("component") == "tool_approval":
                return block
    raise AssertionError("no approval card")
