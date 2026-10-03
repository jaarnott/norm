"""A write shows what it will change before anyone approves it.

Until Oct 2026 the approval card showed a tool's name and its raw JSON
arguments ("Show details"), so approving 37 stock-item updates meant reading
37 JSON blobs of supplier and unit UUIDs. A write that asks first now previews
itself from live data, without writing (services/previews.py): before → after,
ids as names. A call that would fail, or would change nothing, never reaches
the card; the person can approve some rows and not others; and an approved
write whose data moved since the preview stops instead of writing over values
nobody saw.
"""

import copy
import pathlib
import uuid

from app.agents import internal_tools as IT
from app.agents import tool_loop as TL
from app.connectors.function_executor import execute_function, fingerprint
from app.db.config_models import ConnectionSpec
from app.db.models import AutomatedTask, Message, ToolCall
from app.services import approvals, previews
from tests.conftest import (
    _make_membership,
    _make_organization,
    _make_thread,
    _make_user,
)
from tests.test_function_executor_extensions import _wire_fake_connector
from tests.test_tool_loop_unknown_tool import _Block, _Response

_DIR = pathlib.Path(__file__).resolve().parent.parent / "config" / "consolidators"
STOCK = (_DIR / "manage_stock_item.py").read_text()
MENU = (_DIR / "manage_menu.py").read_text()
RECIPE = (_DIR / "record_recipe.py").read_text()

ASK = {"default": "ask", "allow_auto": True, "label": "update stock items"}

SUP_OLD = "18d509da-8292-431e-9bb8-1b2f9ae97a97"
SUP_NEW = "2f0a06ae-70e1-48cb-bd20-07e1048aeb74"
UNIT_KG = "78b83be4-099a-4b0a-8ac2-e223073764a3"
VAR = "afeccce3-6ca4-44dc-33d8-08de57e0406e"

ITEM = {
    "id": "item-1",
    "name": "SAUCE TOMATO CATERING",
    "defaultSupplierId": SUP_OLD,
    "minimumStockOnHandQuantity": 2,
    "suppliers": [
        {
            "id": VAR,
            "supplierId": SUP_OLD,
            "stockCode": "TOM3",
            "unitId": UNIT_KG,
            "unitCost": 16.5,
            "defaultForSupplier": True,
        }
    ],
}

STOCK_ENDPOINTS = [
    {"action": "get_stock_item_full", "method": "GET", "path_template": "//i"},
    {"action": "get_stock_units", "method": "GET", "path_template": "//u"},
    {"action": "get_suppliers", "method": "GET", "path_template": "//s"},
    {"action": "update_stock_item_raw", "method": "PUT", "path_template": "//i"},
    {"action": "create_stock_item_raw", "method": "POST", "path_template": "//i"},
    {"action": "update_variant_unit_raw", "method": "PATCH", "path_template": "//v"},
]
STOCK_READS = {
    "get_stock_item_full": ITEM,
    "get_stock_units": [{"id": UNIT_KG, "name": "Kilo"}],
    "get_suppliers": [
        {"id": SUP_OLD, "name": "Bidfood"},
        {"id": SUP_NEW, "name": "Gilmours"},
    ],
}
STOCK_CONFIG = {
    "max_api_calls": 3,
    "allowed_write_actions": [
        "create_stock_item_raw",
        "update_stock_item_raw",
        "update_variant_unit_raw",
    ],
}

UPDATE = {
    "venue": "Freeman & Grey",
    "op": "update",
    "item_id": "item-1",
    "changes": {"defaultSupplierId": SUP_NEW},
    "variant_changes": [{"variant_id": VAR, "unitCost": 17.95}],
}


def _stock(monkeypatch, reads=None):
    return _wire_fake_connector(
        monkeypatch, copy.deepcopy(reads or STOCK_READS), tools=STOCK_ENDPOINTS
    )


def _rows(card):
    return {c["field"]: (c.get("before"), c["after"]) for c in card["changes"]}


# ---------------------------------------------------------------- sandbox ----


class TestAPreviewRunWritesNothing:
    def test_writes_are_recorded_not_sent(self, monkeypatch, db_session):
        calls = _wire_fake_connector(monkeypatch, {})
        code = (
            "def run(params, call_api, log):\n"
            "    call_api('fake', 'read_thing', {})\n"
            "    out = call_api('fake', 'write_thing', {'price': 12})\n"
            "    return {'sent': out}\n"
        )
        result = execute_function(
            code,
            {},
            db_session,
            None,
            options={"allowed_write_actions": ["write_thing"], "preview": True},
        )
        assert calls == ["read_thing"]
        assert result["preview_writes"] == [
            {"connector": "fake", "action": "write_thing", "params": {"price": 12}}
        ]
        assert result["data"] == {"sent": {"not_sent": True}}

    def test_preview_stops_the_run_even_inside_a_broad_except(
        self, monkeypatch, db_session
    ):
        calls = _wire_fake_connector(monkeypatch, {})
        code = (
            "def run(params, call_api, log):\n"
            "    try:\n"
            "        preview({'title': 'T', 'changes': []})\n"
            "    except Exception:\n"
            "        pass\n"
            "    return call_api('fake', 'write_thing', {})\n"
        )
        result = execute_function(
            code,
            {},
            db_session,
            None,
            options={"allowed_write_actions": ["write_thing"], "preview": True},
        )
        assert result["preview"]["card"] == {"title": "T", "changes": []}
        assert calls == [] and result["preview_writes"] == []

    def test_a_get_declared_write_tool_is_a_write_in_preview(
        self, monkeypatch, db_session
    ):
        """send_report_email is GET-declared; previewing must not send it."""
        calls = _wire_fake_connector(
            monkeypatch,
            {},
            tools=[
                {
                    "action": "send_it",
                    "method": "GET",
                    "effect": "write",
                    "path_template": "//x",
                }
            ],
        )
        code = "def run(params, call_api, log):\n    return call_api('fake', 'send_it', {})\n"
        result = execute_function(
            code,
            {},
            db_session,
            None,
            options={"preview": True, "allowed_write_actions": ["send_it"]},
        )
        assert calls == []
        assert result["preview_writes"][0]["action"] == "send_it"

    def test_a_get_declared_write_tool_must_be_declared(self, monkeypatch, db_session):
        calls = _wire_fake_connector(
            monkeypatch,
            {},
            tools=[
                {
                    "action": "send_it",
                    "method": "GET",
                    "effect": "write",
                    "path_template": "//x",
                }
            ],
        )
        code = "def run(params, call_api, log):\n    return call_api('fake', 'send_it', {})\n"
        result = execute_function(code, {}, db_session, None, options={})
        assert calls == []
        assert "allowed_write_actions" in result["data"]["error"]

    def test_a_batch_shares_its_reads(self, monkeypatch, db_session):
        calls = _wire_fake_connector(monkeypatch, {"read_thing": {"v": 1}})
        code = "def run(params, call_api, log):\n    return call_api('fake', 'read_thing', {'venue': 'X'})\n"
        memo = previews.new_memo()
        for _ in range(3):
            out = execute_function(
                code,
                {},
                db_session,
                None,
                options={"preview": True, "_preview_memo": memo},
            )
            assert out["data"] == {"v": 1}
        assert calls == ["read_thing"]

    def test_outside_an_approval_preview_does_nothing(self, monkeypatch, db_session):
        calls = _wire_fake_connector(monkeypatch, {})
        code = (
            "def run(params, call_api, log):\n"
            "    preview(lambda: call_api('fake', 'read_thing', {}))\n"
            "    return call_api('fake', 'write_thing', {})\n"
        )
        execute_function(
            code,
            {},
            db_session,
            None,
            options={"allowed_write_actions": ["write_thing"]},
        )
        # The card is never built, so its read never happens.
        assert calls == ["write_thing"]


class TestTheApprovedRunChecksFirst:
    CODE = (
        "def run(params, call_api, log):\n"
        "    now = call_api('fake', 'read_thing', {})\n"
        "    preview({'changes': [{'field': 'Price', 'before': now['price'], 'after': 12}]})\n"
        "    return call_api('fake', 'write_thing', {'price': 12})\n"
    )

    def _approved(self, monkeypatch, db_session, seen_price, live_price):
        calls = _wire_fake_connector(monkeypatch, {"read_thing": {"price": live_price}})
        shown = {"changes": [{"field": "Price", "before": seen_price, "after": 12}]}
        result = execute_function(
            self.CODE,
            {},
            db_session,
            None,
            options={
                "allowed_write_actions": ["write_thing"],
                "expect_fingerprint": fingerprint(shown),
            },
        )
        return calls, result

    def test_unchanged_data_writes(self, monkeypatch, db_session):
        calls, result = self._approved(monkeypatch, db_session, 10, 10)
        assert calls == ["read_thing", "write_thing"]
        assert result["success"]

    def test_moved_data_stops_before_writing(self, monkeypatch, db_session):
        calls, result = self._approved(monkeypatch, db_session, 10, 11)
        assert calls == ["read_thing"]
        assert result["success"] is False
        assert result["changed"]["changes"][0]["before"] == 11


# ------------------------------------------------------------ the tools ----


def _preview(code, params, db, config, memo=None):
    return execute_function(
        code,
        params,
        db,
        None,
        options={
            **config,
            "preview": True,
            "_preview_memo": memo or previews.new_memo(),
        },
    )


class TestStockItemPreview:
    def test_an_update_shows_names_not_ids(self, monkeypatch, db_session):
        calls = _stock(monkeypatch)
        out = _preview(STOCK, UPDATE, db_session, STOCK_CONFIG)
        card = out["preview"]["card"]
        assert card["target"] == "SAUCE TOMATO CATERING"
        assert _rows(card) == {
            "Default supplier": ("Bidfood", "Gilmours"),
            "Bidfood TOM3 · Unit cost": ("$16.50", "$17.95"),
        }
        assert "update_stock_item_raw" not in calls

    def test_the_approved_run_writes_when_nothing_moved(self, monkeypatch, db_session):
        _stock(monkeypatch)
        fp = _preview(STOCK, UPDATE, db_session, STOCK_CONFIG)["preview"]["fingerprint"]
        calls = _stock(monkeypatch)
        result = execute_function(
            STOCK,
            dict(UPDATE),
            db_session,
            None,
            options={**STOCK_CONFIG, "expect_fingerprint": fp},
        )
        assert "update_stock_item_raw" in calls
        # Names are only for the card: the approved run doesn't look them up.
        assert "get_suppliers" not in calls
        assert result["success"]

    def test_someone_else_changed_the_cost_so_nothing_is_written(
        self, monkeypatch, db_session
    ):
        _stock(monkeypatch)
        fp = _preview(STOCK, UPDATE, db_session, STOCK_CONFIG)["preview"]["fingerprint"]
        moved = copy.deepcopy(ITEM)
        moved["suppliers"][0]["unitCost"] = 17.0
        calls = _stock(monkeypatch, {**STOCK_READS, "get_stock_item_full": moved})
        result = execute_function(
            STOCK,
            dict(UPDATE),
            db_session,
            None,
            options={**STOCK_CONFIG, "expect_fingerprint": fp},
        )
        assert "update_stock_item_raw" not in calls
        assert result["changed"]["target"] == "SAUCE TOMATO CATERING"

    def test_a_zero_cost_new_variant_is_flagged(self, monkeypatch, db_session):
        _stock(monkeypatch)
        params = {
            "venue": "The Glass Goose",
            "op": "update",
            "item_id": "item-1",
            "add_suppliers": [
                {
                    "supplierId": SUP_NEW,
                    "stockCode": "VEGF2532",
                    "unitId": UNIT_KG,
                    "unitCost": 0,
                    "defaultForSupplier": True,
                }
            ],
        }
        card = _preview(STOCK, params, db_session, STOCK_CONFIG)["preview"]["card"]
        assert _rows(card) == {
            "New supplier variant · Gilmours VEGF2532": (
                None,
                "$0.00 per Kilo (default)",
            )
        }
        assert any("$0.00" in w for w in card["warnings"])


class TestMenuPreview:
    MENU_PAYLOAD = {
        "id": "m-1",
        "name": "Dinner",
        "groups": [
            {
                "name": "Mains",
                "lines": [
                    {"id": "l-1", "name": "Bavette", "workingPrice": 34.0},
                    {"id": "l-2", "name": "Gnocchi", "workingPrice": 28.0},
                ],
            }
        ],
    }
    TOOLS = [
        {"action": "get_menu", "method": "GET", "path_template": "//m"},
        {"action": "update_menu", "method": "PUT", "path_template": "//m"},
        {"action": "delete_menu", "method": "DELETE", "path_template": "//m"},
        {"action": "create_menu", "method": "POST", "path_template": "//m"},
    ]
    CONFIG = {
        "max_api_calls": 3,
        "allowed_write_actions": ["create_menu", "update_menu", "delete_menu"],
    }

    def test_prices_and_removals_read_before_and_after(self, monkeypatch, db_session):
        _wire_fake_connector(
            monkeypatch,
            {"get_menu": copy.deepcopy(self.MENU_PAYLOAD)},
            tools=self.TOOLS,
        )
        params = {
            "venue": "La Zeppa",
            "op": "update",
            "menu_id": "m-1",
            "line_changes": [{"name": "Bavette", "price": "$36"}],
            "remove_lines": ["Gnocchi"],
        }
        card = _preview(MENU, params, db_session, self.CONFIG)["preview"]["card"]
        assert card["target"] == "Dinner"
        assert _rows(card) == {
            "Bavette · price": ("$34.00", "$36.00"),
            "Gnocchi (Mains)": ("$28.00", "Removed"),
        }

    def test_a_delete_says_what_goes(self, monkeypatch, db_session):
        _wire_fake_connector(
            monkeypatch,
            {"get_menu": copy.deepcopy(self.MENU_PAYLOAD)},
            tools=self.TOOLS,
        )
        params = {
            "venue": "La Zeppa",
            "op": "delete",
            "menu_id": "m-1",
            "name": "Dinner",
        }
        card = _preview(MENU, params, db_session, self.CONFIG)["preview"]["card"]
        assert _rows(card) == {"Menu": ("1 sections, 2 dishes", "Deleted")}


class TestRecipePreview:
    RECIPE_PAYLOAD = {
        "success": True,
        "data": {
            "recipe": {
                "recipe": {
                    "id": "r-1",
                    "name": "COMPONENT - AIOLI",
                    "notes": "<p>x</p>",
                },
                "version": {
                    "yield_quantity": 20,
                    "yield_unit_name": "Litre",
                    "lines": [
                        {
                            "line_id": "a",
                            "name": "OIL CANOLA",
                            "quantity": 16,
                            "unit_name": "Litre",
                        },
                        {
                            "line_id": "b",
                            "name": "GARLIC PEELED",
                            "quantity": 500,
                            "unit_name": "Gram",
                        },
                        {
                            "line_id": "c",
                            "name": "SALT TABLE IODISED",
                            "quantity": 80,
                            "unit_name": "Gram",
                        },
                    ],
                },
                "allergens": [{"code": "EGGS", "status": "no"}],
            }
        },
    }
    TOOLS = [
        {"action": "kitchen_get_recipe", "method": "GET", "path_template": "//r"},
        {"action": "kitchen_record_recipe", "method": "POST", "path_template": "//r"},
    ]
    CONFIG = {
        "max_api_calls": 2,
        "allowed_write_actions": ["cook_brothers_app.kitchen_record_recipe"],
    }

    def _card(self, monkeypatch, db_session, params):
        _wire_fake_connector(
            monkeypatch,
            {"kitchen_get_recipe": copy.deepcopy(self.RECIPE_PAYLOAD)},
            tools=self.TOOLS,
        )
        return _preview(RECIPE, params, db_session, self.CONFIG)["preview"]["card"]

    def test_a_merge_changes_one_line_and_names_the_allergen(
        self, monkeypatch, db_session
    ):
        card = self._card(
            monkeypatch,
            db_session,
            {
                "recipe_id": "r-1",
                "mode": "merge",
                "ingredients": [
                    {
                        "kind": "item",
                        "name": "Garlic peeled",
                        "quantity": 600,
                        "unit": "Gram",
                    }
                ],
                "allergens": [{"code": "EGGS", "status": "contains"}],
            },
        )
        assert _rows(card) == {
            "Ingredient · Garlic peeled": ("500 Gram", "600 Gram"),
            "Allergen · Eggs": ("No", "Contains"),
        }

    def test_a_replace_lists_the_lines_it_drops(self, monkeypatch, db_session):
        card = self._card(
            monkeypatch,
            db_session,
            {
                "recipe_id": "r-1",
                "mode": "replace",
                "ingredients": [
                    {
                        "kind": "item",
                        "name": "OIL CANOLA",
                        "quantity": 16,
                        "unit": "Litre",
                    }
                ],
            },
        )
        assert _rows(card) == {
            "Ingredient · GARLIC PEELED": ("500 Gram", "Removed"),
            "Ingredient · SALT TABLE IODISED": ("80 Gram", "Removed"),
        }
        assert "2 current line(s) are removed" in card["warnings"][0]


# ----------------------------------------------------- preview outcomes ----


def _row(**fields):
    return {
        "action": "manage_stock_item",
        "method": "PUT",
        "effect": "write",
        "approval": ASK,
        **fields,
    }


class TestOutcomes:
    def test_a_call_that_would_fail_is_refused_before_anyone_is_asked(
        self, monkeypatch, db_session
    ):
        calls = _stock(monkeypatch)
        row = _row(consolidator_config={**STOCK_CONFIG, "function_code": STOCK})
        out = previews.preview_call(
            "loadedhub", "manage_stock_item", {"op": "update"}, row, db_session, None
        )
        assert out.kind == "refused"
        assert "item_id" in out.result["error"]
        assert calls == []

    def test_a_call_that_changes_nothing_is_simply_done(self, monkeypatch, db_session):
        _stock(monkeypatch)
        row = _row(consolidator_config={**STOCK_CONFIG, "function_code": STOCK})
        out = previews.preview_call(
            "loadedhub",
            "manage_stock_item",
            {
                "op": "update",
                "item_id": "item-1",
                "changes": {"defaultSupplierId": SUP_OLD},
            },
            row,
            db_session,
            None,
        )
        assert out.kind == "done"
        assert out.result["data"]["result"] == "no differences — nothing written"

    def test_a_tool_without_a_preview_shows_its_writes_labelled(
        self, monkeypatch, db_session
    ):
        _wire_fake_connector(monkeypatch, {})
        code = (
            "def run(params, call_api, log):\n"
            "    return call_api('fake', 'write_thing', {'minimum_on_hand': 6, 'venue': 'X'})\n"
        )
        row = _row(
            consolidator_config={
                "function_code": code,
                "allowed_write_actions": ["write_thing"],
            }
        )
        out = previews.preview_call("fake", "anything", {}, row, db_session, None)
        assert out.kind == "card"
        assert out.card["source"] == "writes"
        assert _rows(out.card) == {"Write thing · Minimum on hand": (None, "6")}
        assert out.card["title"] == "Update stock items"

    def test_a_broken_preview_still_asks_with_the_values(self, monkeypatch, db_session):
        def boom(params, db, thread_id):
            raise RuntimeError("Loaded timed out")

        monkeypatch.setitem(IT._PREVIEWERS, ("x", "w"), boom)
        out = previews.preview_call("x", "w", {"to": "a@b.c"}, None, db_session, None)
        assert out.kind == "card"
        assert "Loaded timed out" in out.card["warnings"][0]
        assert _rows(out.card) == {"To": (None, "a@b.c")}


class TestBuiltInPreviews:
    def test_a_report_email_keeps_the_message_it_showed(self, db_session, admin_user):
        thread = _make_thread(
            db_session, admin_user, domain="norm", intent="norm.tool_use"
        )
        report = Message(
            thread_id=thread.id, role="assistant", content="# Sales\nUp 4%"
        )
        db_session.add(report)
        db_session.flush()
        out = previews.preview_call(
            "norm_email",
            "send_report_email",
            {"to": "dianna@cbhg.co.nz", "subject": "Weekly"},
            None,
            db_session,
            thread.id,
        )
        assert out.card["note"] == "Sales Up 4%"
        assert out.run_params["content_message_id"] == report.id
        # A newer message (the approval card's) no longer changes what is sent.
        db_session.add(
            Message(thread_id=thread.id, role="assistant", content="Approve?")
        )
        db_session.flush()
        sent = IT._report_source_message(out.run_params, db_session, thread.id)
        assert sent.id == report.id

    def test_another_organisations_task_is_not_found(self, db_session):
        mine, theirs = _make_organization(db_session), _make_organization(db_session)
        me, them = _make_user(db_session), _make_user(db_session)
        _make_membership(db_session, me, mine)
        _make_membership(db_session, them, theirs)
        task = AutomatedTask(
            title="Their report", agent_slug="norm", prompt="p", created_by=them.id
        )
        db_session.add(task)
        db_session.flush()
        thread = _make_thread(db_session, me, domain="norm", intent="norm.tool_use")
        params = {"op": "update", "task_id": task.id, "title": "Mine now"}
        out = previews.preview_call(
            "norm", "manage_task", params, None, db_session, thread.id
        )
        assert out.kind == "refused"
        handled = IT._manage_task(params, db_session, thread.id)
        assert handled["success"] is False
        assert task.title == "Their report"

    def test_a_task_change_reads_before_and_after(self, db_session, admin_user):
        task = AutomatedTask(
            title="Sales report",
            agent_slug="norm",
            prompt="p",
            created_by=admin_user.id,
        )
        db_session.add(task)
        db_session.flush()
        thread = _make_thread(
            db_session, admin_user, domain="norm", intent="norm.tool_use"
        )
        out = previews.preview_call(
            "norm",
            "manage_task",
            {"op": "update", "task_id": task.id, "status": "paused"},
            None,
            db_session,
            thread.id,
        )
        assert out.card["target"] == "Sales report"
        assert _rows(out.card) == {"Status": ("draft", "paused")}


# ------------------------------------------------------------- the loop ----


def _spec(db, action, **row):
    name = f"t{uuid.uuid4().hex[:10]}"
    db.add(
        ConnectionSpec(
            connector_name=name,
            display_name=name,
            execution_mode="internal",
            auth_type="none",
            tools=[
                {
                    "action": action,
                    "method": "GET",
                    "effect": "write",
                    "approval": ASK,
                    **row,
                }
            ],
        )
    )
    db.flush()
    return name


def _turn(db, user, monkeypatch, connector, action, inputs, said="Fixing the price."):
    """One model turn that calls the tool (``inputs`` = one dict per call)."""
    seen = []

    def llm(system_prompt, messages, tools, db, thread_id, call_type):
        seen.append(messages[-1])
        if len(seen) == 1:
            blocks = [_Block("text", text=said)] + [
                _Block(
                    "tool_use",
                    name=f"{connector}__{action}",
                    id=f"toolu_{uuid.uuid4().hex[:8]}",
                    input=i,
                )
                for i in inputs
            ]
            return _Response("tool_use", blocks), None
        return _Response("end_turn", [_Block("text", text="Done.")]), None

    monkeypatch.setattr("app.interpreter.llm_interpreter.call_llm_with_tools", llm)
    thread = _make_thread(
        db, user, domain="norm", intent="norm.tool_use", status="in_progress"
    )
    tool = {
        "name": f"{connector}__{action}",
        "description": "[GET] test",
        "input_schema": {"type": "object", "properties": {}},
    }
    TL.run_tool_loop("go", thread, db, "sys", [tool], config_db=db)
    return thread, seen


def _price_previewer(before):
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


class TestTheCard:
    def test_the_card_shows_the_preview_under_norms_own_words(
        self, db_session, admin_user, monkeypatch
    ):
        conn = _spec(db_session, "set_price")
        monkeypatch.setitem(IT._PREVIEWERS, (conn, "set_price"), _price_previewer(34))
        thread, _ = _turn(
            db_session, admin_user, monkeypatch, conn, "set_price", [{"price": 36}]
        )

        assert thread.status == approvals.AWAITING
        tc = db_session.query(ToolCall).filter(ToolCall.thread_id == thread.id).one()
        assert _rows(tc.preview) == {"Price": ("34", "36")}
        assert tc.preview["fingerprint"]
        msg = (
            db_session.query(Message)
            .filter(Message.thread_id == thread.id, Message.role == "assistant")
            .one()
        )
        assert msg.content == "Fixing the price."
        (card,) = [b for b in msg.display_blocks if b["component"] == "tool_approval"]
        assert card["data"]["tool_calls"][0]["preview"]["title"] == "Change a price"

    def test_a_call_that_would_fail_goes_back_to_norm_not_to_a_card(
        self, db_session, admin_user, monkeypatch
    ):
        conn = _spec(db_session, "set_price")
        monkeypatch.setitem(
            IT._PREVIEWERS,
            (conn, "set_price"),
            lambda p, d, t: {"error": "no such dish"},
        )
        thread, seen = _turn(
            db_session, admin_user, monkeypatch, conn, "set_price", [{"price": 36}]
        )
        assert thread.status != approvals.AWAITING
        assert "no such dish" in str(seen[1])
        tc = db_session.query(ToolCall).filter(ToolCall.thread_id == thread.id).one()
        assert tc.status == "failed"


def _card_of(db, thread):
    """The approval card block in ``thread``."""
    for msg in db.query(Message).filter(Message.thread_id == thread.id).all():
        for block in msg.display_blocks or []:
            if block.get("component") == "tool_approval":
                return block
    raise AssertionError("no approval card")


class TestDeciding:
    def _suspended(self, db_session, user, monkeypatch, prices=(36, 30)):
        conn = _spec(db_session, "set_price")
        monkeypatch.setitem(IT._PREVIEWERS, (conn, "set_price"), _price_previewer(34))
        ran = []

        def handler(params, db, thread_id):
            ran.append(params)
            return {"success": True, "data": {"ok": True}}

        monkeypatch.setitem(IT._REGISTRY, (conn, "set_price"), handler)
        thread, _ = _turn(
            db_session,
            user,
            monkeypatch,
            conn,
            "set_price",
            [{"price": p, "dish": f"Dish {p}"} for p in prices],
        )
        calls = (
            db_session.query(ToolCall)
            .filter(ToolCall.thread_id == thread.id)
            .order_by(ToolCall.created_at)
            .all()
        )
        return conn, thread, calls, ran

    def _resume(self, db_session, thread):
        TL.resume_tool_loop(thread, db_session, "sys", [], config_db=db_session)

    def test_approving_some_rows_declines_the_rest_by_name(
        self, db_session, admin_user, monkeypatch
    ):
        _, thread, (first, second), ran = self._suspended(
            db_session, admin_user, monkeypatch
        )
        approvals.claim(db_session, thread)
        approvals.decide(
            db_session, thread, admin_user, approve=True, tool_call_ids=[first.id]
        )

        assert first.status == "approved"
        assert second.status == "rejected"
        assert "left this one out" in second.error_message
        self._resume(db_session, thread)
        assert [p["price"] for p in ran] == [36]

        card = _card_of(db_session, thread)
        rows = {r["id"]: r for r in card["data"]["tool_calls"]}
        assert rows[first.id]["status"] == "approved"
        assert rows[first.id]["outcome"] == "done"
        assert rows[second.id]["status"] == "rejected"

    def test_a_decline_reason_reaches_norm(self, db_session, admin_user, monkeypatch):
        _, thread, (first, _second), _ = self._suspended(
            db_session, admin_user, monkeypatch
        )
        approvals.claim(db_session, thread)
        approvals.decide(
            db_session, thread, admin_user, approve=False, notes="wrong venue"
        )
        assert "wrong venue" in first.error_message

    def test_an_approved_change_whose_data_moved_is_not_written(
        self, db_session, admin_user, monkeypatch
    ):
        conn, thread, (first, _second), ran = self._suspended(
            db_session, admin_user, monkeypatch
        )
        # Someone changed the price in Loaded after the card was shown.
        monkeypatch.setitem(IT._PREVIEWERS, (conn, "set_price"), _price_previewer(35))
        approvals.claim(db_session, thread)
        approvals.decide(db_session, thread, admin_user, approve=True)
        self._resume(db_session, thread)

        assert ran == []
        assert first.status == "failed"
        assert "changed after it was approved" in first.error_message
        card = _card_of(db_session, thread)
        assert {r["outcome"] for r in card["data"]["tool_calls"]} == {"changed"}

    def test_the_route_takes_the_ticked_rows(
        self, client, db_session, admin_user, monkeypatch
    ):
        from app.auth.security import create_access_token

        _, thread, (first, second), ran = self._suspended(
            db_session, admin_user, monkeypatch
        )
        monkeypatch.setattr(
            TL, "_execute_loop", lambda *a, **k: {"status": "completed"}
        )
        r = client.post(
            f"/api/threads/{thread.id}/approve",
            json={"tool_call_ids": [second.id]},
            headers={
                "Authorization": f"Bearer {create_access_token({'sub': admin_user.id})}"
            },
        )
        assert r.status_code == 200, r.text
        assert [p["price"] for p in ran] == [30]
        assert db_session.get(ToolCall, first.id).status == "rejected"


class TestEveryWriteThatAsksCanPreview:
    def test_a_consolidator_without_preview_is_reported(self):
        from app.services.config_validator import check_approval_labels

        row = _row(
            consolidator_config={"function_code": "def run(p, c, l):\n    pass\n"}
        )
        issues = check_approval_labels(
            [("loadedhub", [row])], {"loadedhub.manage_stock_item": "stock"}
        )
        assert ["preview(" in i.problem for i in issues] == [True]

    def test_the_write_tools_that_ask_all_preview(self):
        from app.services.config_validator import check_approval_labels

        for code in (STOCK, MENU, RECIPE):
            row = _row(consolidator_config={"function_code": code})
            assert (
                check_approval_labels(
                    [("loadedhub", [row])], {"loadedhub.manage_stock_item": "stock"}
                )
                == []
            )
        for connector, action in (
            ("gmail", "send_email"),
            ("norm_email", "send_report_email"),
            ("norm", "manage_task"),
            ("norm", "set_approval_preference"),
        ):
            row = {"action": action, "effect": "write", "approval": ASK}
            assert (
                check_approval_labels(
                    [(connector, [row])], {f"{connector}.{action}": "app"}
                )
                == []
            )
