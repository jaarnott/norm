"""The App Builder's style rules stay in step with what the app host provides.

scripts/sync_app_builder_style.py patches the live `build_an_app` skill; the
same text is mirrored in scripts/sync_app_builder_agent.py. The icon list it
gives the builder must be exactly the names the web app can draw — a name it
doesn't know falls back to a generic window icon on every page that lists the
app.

The patch itself was applied to the shared config DB on 11 Oct 2026 (every
swap's new text is live, none of the old), so its one-shot mechanics — the
swap and the hand-edit guard — are no longer tested here; what stays is the
drift between the script, the repo prompt and the web icon table.
"""

import importlib.util
import pathlib
import re

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


def test_icon_names_match_the_web_icon_table():
    src = (REPO / "apps/web/app/components/ui/appIcons.ts").read_text()
    start = src.index("const BY_NAME")
    body = src[start : src.index("};", start)]
    names = [
        a or b for a, b in re.findall(r"(?:'([a-z0-9-]+)'|\b([a-z]+)):\s*[A-Z]", body)
    ]
    assert sorted(names) == sorted(style.ICON_NAMES)


def test_the_repo_prompt_already_carries_the_new_rules():
    _new, changes = style.patch_instructions(agent.SYSTEM_PROMPT)
    assert changes == []
    assert "system-ui" not in agent.SYSTEM_PROMPT
    assert "one emoji" not in agent.SYSTEM_PROMPT
    save_app = next(t for t in agent.BUILDER_TOOLS if t["action"] == "save_app")
    assert save_app["field_descriptions"]["icon"] == style.SAVE_APP_ICON_NEW


def test_the_storage_section_is_in_the_repo_prompt_too():
    """Apps can keep records; the live skill and the repo prompt both say so,
    in the same words (thread 029ca35b, 20 Oct 2026: Norm said they couldn't)."""
    storage = _load("sync_app_builder_storage")
    assert storage.STORAGE_SECTION in agent.SYSTEM_PROMPT
    assert agent.SYSTEM_PROMPT.index(
        storage.STORAGE_SECTION
    ) < agent.SYSTEM_PROMPT.index(storage.ANCHOR)
    # applying it twice changes nothing
    once, changed = storage.patch(
        agent.SYSTEM_PROMPT.replace(storage.STORAGE_SECTION, "")
    )
    assert changed and storage.patch(once) == (once, False)


def test_the_role_migration_gives_what_the_code_defines():
    """Role permissions are written to the database once; a scope added to the
    code later needs a migration. The app scopes' one matches the code."""
    import importlib.util as _u

    from app.auth.permissions import STANDARD_ROLES

    path = (
        REPO / "apps/api/alembic/versions/vc8e0g2i4k6m_app_scopes_for_system_roles.py"
    )
    spec = _u.spec_from_file_location("mig", path)
    mig = _u.module_from_spec(spec)
    spec.loader.exec_module(mig)
    for role, defn in STANDARD_ROLES.items():
        want = {p for p in defn["permissions"] if p.startswith("apps:")}
        assert set(mig.GRANTS.get(role, ())) == want, role
