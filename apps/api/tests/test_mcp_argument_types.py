"""MCP argument types: discovery keeps them, the call path casts to them.

Orbit's (cook_brothers_app) zod schemas reject "true" for a boolean and "100"
for a number. Discovery used to keep only descriptions, so the agent was
shown every field as a string; on 26 Sep 2026 three stock_find_stocktakes
calls failed on exactly that before the agent stopped passing the fields.
"""

from app.connectors.mcp_executor import (
    coerce_arguments_to_schema,
    convert_mcp_tools_to_spec,
)

TOOL = {
    "name": "stock_find_stocktakes",
    "description": "List stocktakes.",
    "inputSchema": {
        "type": "object",
        "properties": {
            "venue_id": {"type": "string", "description": "Venue ID"},
            "from": {"type": "string"},
            "status": {"type": "string", "enum": ["pending", "completed", "all"]},
            "include_templates": {"type": "boolean", "description": "Templates too"},
            "limit": {"type": "number", "description": "Max rows"},
            "include": {
                "type": "array",
                "items": {"type": "string", "enum": ["areas"]},
            },
        },
        "required": ["venue_id"],
    },
}


class TestDiscoveryKeepsTypes:
    def test_non_string_and_enum_fields_get_a_field_schema(self):
        row = convert_mcp_tools_to_spec([TOOL])[0]
        assert row["field_schema"] == {
            "status": {"type": "string", "enum": ["pending", "completed", "all"]},
            "include_templates": {"type": "boolean"},
            "limit": {"type": "number"},
            "include": {
                "type": "array",
                "items": {"type": "string", "enum": ["areas"]},
            },
        }
        # Plain strings stay implicit — the row would only bloat.
        assert "venue_id" not in row["field_schema"]
        assert "from" not in row["field_schema"]

    def test_descriptions_still_carry_the_enum_hint(self):
        row = convert_mcp_tools_to_spec([TOOL])[0]
        assert "Options: pending, completed, all" in row["field_descriptions"]["status"]
        assert row["required_fields"] == ["venue_id"]

    def test_a_tool_of_plain_strings_gets_no_field_schema(self):
        row = convert_mcp_tools_to_spec(
            [{"name": "t", "inputSchema": {"properties": {"q": {"type": "string"}}}}]
        )[0]
        assert "field_schema" not in row


class TestCallPathCasts:
    OP = convert_mcp_tools_to_spec([TOOL])[0]

    def test_the_incident_shape_is_cast(self):
        out = coerce_arguments_to_schema(
            {"status": "completed", "include_templates": "true", "limit": "100"},
            self.OP,
        )
        assert out == {"status": "completed", "include_templates": True, "limit": 100}

    def test_false_zero_and_fractions(self):
        out = coerce_arguments_to_schema(
            {"include_templates": "False", "limit": "2.5"}, self.OP
        )
        assert out == {"include_templates": False, "limit": 2.5}

    def test_arrays_from_json_or_commas(self):
        assert coerce_arguments_to_schema({"include": '["areas"]'}, self.OP) == {
            "include": ["areas"]
        }
        assert coerce_arguments_to_schema({"include": "areas, recipes"}, self.OP) == {
            "include": ["areas", "recipes"]
        }

    def test_already_typed_and_unknown_fields_pass_through(self):
        out = coerce_arguments_to_schema(
            {"include_templates": True, "limit": 7, "venue_id": "v1", "extra": "x"},
            self.OP,
        )
        assert out == {
            "include_templates": True,
            "limit": 7,
            "venue_id": "v1",
            "extra": "x",
        }

    def test_an_uncastable_value_is_sent_as_is(self):
        out = coerce_arguments_to_schema(
            {"limit": "lots", "include_templates": "maybe"}, self.OP
        )
        assert out == {"limit": "lots", "include_templates": "maybe"}

    def test_no_field_schema_means_no_change(self):
        assert coerce_arguments_to_schema({"limit": "100"}, {"action": "x"}) == {
            "limit": "100"
        }
