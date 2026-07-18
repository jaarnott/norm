"""build_input_schema — the shared config-row -> JSON Schema projection.

Extracted from build_tool_definitions so Norm's own agents and the external
MCP surface share one implementation (Anthropic `input_schema` and MCP
`inputSchema` are the same object). These tests pin the exact output shape:
the extraction had no test coverage before it, and a silent change here
alters what every agent sees.
"""

from app.agents.prompt_builder import build_input_schema, build_venue_property


class TestFieldProjection:
    def test_empty_tool(self):
        assert build_input_schema(
            {"required_fields": [], "optional_fields": [], "field_mapping": {}}
        ) == {
            "type": "object",
            "properties": {},
            "required": [],
            "additionalProperties": False,
        }

    def test_fields_default_to_string(self):
        schema = build_input_schema(
            {
                "required_fields": ["start_date"],
                "optional_fields": [],
                "field_mapping": {},
            }
        )
        assert schema["properties"]["start_date"] == {
            "type": "string",
            "description": "start_date",
        }

    def test_required_and_optional_both_become_properties(self):
        schema = build_input_schema(
            {
                "required_fields": ["a"],
                "optional_fields": ["b"],
                "field_mapping": {},
            }
        )
        assert set(schema["properties"]) == {"a", "b"}
        # ...but only required_fields is `required`
        assert schema["required"] == ["a"]

    def test_field_mapping_appends_api_field_hint(self):
        schema = build_input_schema(
            {
                "required_fields": ["start_date"],
                "optional_fields": [],
                "field_mapping": {"start_date": "from"},
            }
        )
        assert schema["properties"]["start_date"]["description"] == (
            "Maps to API field: from"
        )

    def test_identity_mapping_adds_no_hint(self):
        schema = build_input_schema(
            {
                "required_fields": ["d"],
                "optional_fields": [],
                "field_mapping": {"d": "d"},
                "field_descriptions": {"d": "A date"},
            }
        )
        assert schema["properties"]["d"]["description"] == "A date"

    def test_description_and_mapping_are_joined(self):
        schema = build_input_schema(
            {
                "required_fields": ["a"],
                "optional_fields": [],
                "field_mapping": {"a": "apiA"},
                "field_descriptions": {"a": "The A field"},
            }
        )
        assert schema["properties"]["a"]["description"] == (
            "The A field. Maps to API field: apiA"
        )

    def test_always_closed(self):
        """additionalProperties must stay False — it's what makes strict tool
        use and MCP argument validation meaningful."""
        assert (
            build_input_schema(
                {"required_fields": ["a"], "optional_fields": [], "field_mapping": {}}
            )["additionalProperties"]
            is False
        )

    def test_required_is_a_copy_not_an_alias(self):
        """Mutating the returned schema must not corrupt the config row."""
        tool = {"required_fields": ["a"], "optional_fields": [], "field_mapping": {}}
        build_input_schema(tool)["required"].append("injected")
        assert tool["required_fields"] == ["a"]


class TestFieldSchemaPassthrough:
    """field_schema is the only way a spec row can express a non-string type."""

    def test_nested_array_passes_through_verbatim(self):
        nested = {
            "type": "array",
            "items": {"type": "object", "properties": {"sku": {"type": "string"}}},
        }
        schema = build_input_schema(
            {
                "required_fields": ["lines"],
                "optional_fields": [],
                "field_mapping": {},
                "field_schema": {"lines": nested},
            }
        )
        assert schema["properties"]["lines"]["type"] == "array"
        assert schema["properties"]["lines"]["items"] == nested["items"]

    def test_field_schema_gets_description_from_field_descriptions(self):
        schema = build_input_schema(
            {
                "required_fields": ["lines"],
                "optional_fields": [],
                "field_mapping": {},
                "field_descriptions": {"lines": "Order lines"},
                "field_schema": {"lines": {"type": "array"}},
            }
        )
        assert schema["properties"]["lines"]["description"] == "Order lines"

    def test_field_schema_own_description_wins(self):
        schema = build_input_schema(
            {
                "required_fields": ["x"],
                "optional_fields": [],
                "field_mapping": {},
                "field_descriptions": {"x": "should not win"},
                "field_schema": {"x": {"type": "integer", "description": "own wins"}},
            }
        )
        assert schema["properties"]["x"]["description"] == "own wins"

    def test_field_schema_is_not_mutated(self):
        """The row is shared config — projecting it must not write back."""
        field_schema = {"lines": {"type": "array"}}
        build_input_schema(
            {
                "required_fields": ["lines"],
                "optional_fields": [],
                "field_mapping": {},
                "field_descriptions": {"lines": "Order lines"},
                "field_schema": field_schema,
            }
        )
        assert field_schema == {"lines": {"type": "array"}}
        assert "description" not in field_schema["lines"]

    def test_field_schema_bypasses_field_mapping_hint(self):
        schema = build_input_schema(
            {
                "required_fields": ["lines"],
                "optional_fields": [],
                "field_mapping": {"lines": "orderLines"},
                "field_schema": {"lines": {"type": "array"}},
            }
        )
        assert "Maps to API field" not in schema["properties"]["lines"]["description"]


class TestExtraProperties:
    def test_merged_after_fields(self):
        schema = build_input_schema(
            {"required_fields": ["a"], "optional_fields": [], "field_mapping": {}},
            {"venue": build_venue_property(["La Zeppa", "Little High"])},
        )
        assert set(schema["properties"]) == {"a", "venue"}
        assert schema["properties"]["venue"]["enum"] == ["La Zeppa", "Little High"]

    def test_extra_properties_are_not_required(self):
        schema = build_input_schema(
            {"required_fields": ["a"], "optional_fields": [], "field_mapping": {}},
            {"venue": build_venue_property(["X"])},
        )
        assert schema["required"] == ["a"]

    def test_none_extra_is_a_no_op(self):
        tool = {"required_fields": ["a"], "optional_fields": [], "field_mapping": {}}
        assert build_input_schema(tool, None) == build_input_schema(tool)


class TestMissingKeysAreTolerated:
    """Config rows are hand-edited; a missing optional key must not 500."""

    def test_minimal_row(self):
        assert build_input_schema({"required_fields": ["a"]})["properties"]["a"] == {
            "type": "string",
            "description": "a",
        }

    def test_totally_empty_row(self):
        assert build_input_schema({})["properties"] == {}
