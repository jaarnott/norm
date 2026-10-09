"""The App Builder's style rules stay in step with what the app host provides.

scripts/sync_app_builder_style.py patches the live `build_an_app` skill; the
same text is mirrored in scripts/sync_app_builder_agent.py. The icon list it
gives the builder must be exactly the names the web app can draw — a name it
doesn't know falls back to a generic window icon on every page that lists the
app.
"""

import importlib.util
import pathlib
import re

import pytest

REPO = pathlib.Path(__file__).resolve().parents[3]


def _load(name: str):
    spec = importlib.util.spec_from_file_location(
        name, REPO / f"apps/api/scripts/{name}.py"
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


style = _load("sync_app_builder_style")
agent = _load("sync_app_builder_agent")


def _old_skill() -> str:
    """The skill as it read before the refresh — every swap's old text."""
    return "\n".join(
        [
            "## What an app is",
            "`save_app` takes: `name`, `slug`, " + style.ICON_OLD + ", `spec`.",
            "## Writing ui_source",
            style.STYLE_OLD + style.FAILURE_OLD,
            "## Skeleton",
            style.ROOT_OLD + "<script>",
            style.CATCH_OLD + "</script>",
        ]
    )


def test_icon_names_match_the_web_icon_table():
    src = (REPO / "apps/web/app/components/ui/appIcons.ts").read_text()
    start = src.index("const BY_NAME")
    body = src[start : src.index("};", start)]
    names = [
        a or b for a, b in re.findall(r"(?:'([a-z0-9-]+)'|\b([a-z]+)):\s*[A-Z]", body)
    ]
    assert sorted(names) == sorted(style.ICON_NAMES)


def test_patch_swaps_every_old_line_and_is_idempotent():
    new, changes = style.patch_instructions(_old_skill())
    assert len(changes) == len(style.SWAPS)
    for _label, old, replacement in style.SWAPS:
        assert old not in new
        assert replacement in new
    again, more = style.patch_instructions(new)
    assert again == new and more == []


def test_a_hand_edited_line_stops_the_patch():
    text = _old_skill().replace(style.STYLE_OLD, "- Style: whatever we said.\n")
    with pytest.raises(SystemExit, match="style rules"):
        style.patch_instructions(text)


def test_the_repo_prompt_already_carries_the_new_rules():
    _new, changes = style.patch_instructions(agent.SYSTEM_PROMPT)
    assert changes == []
    assert "system-ui" not in agent.SYSTEM_PROMPT
    assert "one emoji" not in agent.SYSTEM_PROMPT
    save_app = next(t for t in agent.BUILDER_TOOLS if t["action"] == "save_app")
    assert save_app["field_descriptions"]["icon"] == style.SAVE_APP_ICON_NEW
