"""App Builder builds apps that look like Norm (Oct 2026 UI refresh).

The App Builder's live instructions are the `build_an_app` skill in the config
DB (created once by sync_unified_prompt.py, patched in place since), plus the
`save_app` tool entry on the norm spec. Both still describe the pre-refresh
look: a system-ui font, hard-coded greys and Norm green, root padding, and
"one emoji" for the icon. Since the refresh the host (AppRunner) injects
Norm's font, `--norm-*` tokens and `.n-*` classes, draws the app's title and
page gutter itself, and resolves the icon as a line-icon name. The sandbox
also blocks alert/confirm/prompt, which the skill never mentions.

This swaps those exact lines — and fails, naming the line, if one isn't there
(edit by hand then). Idempotent. The config DB is shared by every environment,
so a real run reaches production at once: dry-run first.

The same text is mirrored in scripts/sync_app_builder_agent.py (the repo copy
of the prompt) so the two never drift; tests/test_app_builder_style.py checks
both, and that ICON_NAMES matches the web app's icon table.

Usage:
    uv run python scripts/sync_app_builder_style.py --dry-run
    uv run python scripts/sync_app_builder_style.py
"""

import argparse
import difflib
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

# The line-icon names an app may pick — the keys of BY_NAME in
# apps/web/app/components/ui/appIcons.ts (a test keeps the two equal).
ICON_NAMES = [
    "app-window", "award", "banknote", "beer", "bell", "blocks", "book-open",
    "boxes", "calendar-clock", "calendar-days", "chart-column", "chart-column-big",
    "chart-line", "chef-hat", "clipboard-list", "clock", "coffee", "compass",
    "concierge-bell", "contact", "cooking-pot", "folder-open", "graduation-cap",
    "hand-platter", "handshake", "heart", "layout-dashboard", "mail", "martini",
    "megaphone", "package", "puzzle", "receipt", "salad", "share-2",
    "shield-check", "shopping-cart", "smartphone", "soup", "sparkles", "star",
    "store", "target", "trending-up", "truck", "user-round", "user-round-search",
    "users", "utensils", "utensils-crossed", "wine",
]  # fmt: skip

ICON_OLD = "`icon` (one emoji)"
ICON_NEW = "`icon` (a line-icon name from the list under Style — never an emoji)"

STYLE_OLD = (
    "- Style: system-ui font, minimal chrome, #2a2a2a text on white, muted greys "
    "(#8a8a8a), Norm green #2e7d4f for positives, amber #b45309 for warnings. "
    "Inline SVG for charts — no chart libraries. Currency as $X,XXX.\n"
)
STYLE_NEW = (
    "- **Style comes from Norm.** Norm injects its font, colour tokens and classes "
    "ahead of your markup, so an app looks like the rest of Norm without styling "
    "of its own. Never set a font or hard-code a colour. Colours: "
    "`var(--norm-text)`, `--norm-text-soft`, `--norm-muted`, `--norm-line`, "
    "`--norm-bg`, `--norm-surface-alt`, `--norm-accent`; for status `--norm-ok`, "
    "`--norm-warn`, `--norm-error`, `--norm-info`, each with a `-bg` tint. Sizes "
    "`--norm-fs-xs|sm|base|md|lg`; radii `--norm-radius-sm`, `--norm-radius`, "
    "`--norm-radius-lg`. Use the classes rather than styling your own: `.n-btn` "
    "with `--primary`, `--secondary`, `--quiet`, `--danger`, `--sm`; `.n-badge` "
    "with `--ok`, `--warn`, `--error`, `--info`; `.n-card`; `.n-table` (number "
    'cells `class="num"`); `.n-tabs` with `.n-tab`; `.n-input`, `.n-select`, '
    "`.n-label`. At most one primary button per screen. Inline SVG for charts, "
    "coloured from the tokens — no chart libraries. Currency as $X,XXX.\n"
    "- **No outer padding and no title.** Norm draws the app's name above it and "
    "gives it the page gutter. Start with the content: no padding, margin or "
    "max-width on your root, and no heading that repeats the app's name. "
    "Headings for sections inside the app are fine.\n"
    "- **Never call `alert()`, `confirm()` or `prompt()`.** The sandbox blocks "
    "them, so the button would silently do nothing. Confirm with an in-page "
    "dialog (an overlaid `.n-card` with a `.n-btn--danger` and a "
    "`.n-btn--secondary`), take text in an `.n-input`, and show results inline.\n"
    "- **Icon.** `icon` is one of these line-icon names: "
    + ", ".join(ICON_NAMES)
    + ". Don't decorate the app itself with emoji.\n"
)

FAILURE_OLD = (
    '- Show real failures plainly ("✗ <message>") — an honest error beats an '
    "empty chart.\n"
)
FAILURE_NEW = (
    "- Show real failures plainly — the real message, in `var(--norm-error)` — an "
    "honest error beats an empty chart.\n"
)

ROOT_OLD = (
    '<div id="app" style="padding:1.2rem;font-family:system-ui">'
    '<div id="out">loading…</div></div>\n'
)
ROOT_NEW = '<div id="app"><div id="out">loading…</div></div>\n'

CATCH_OLD = (
    "      .catch(function (e) { document.getElementById('out').textContent = "
    "'✗ ' + e.message; });\n"
)
CATCH_NEW = (
    "      .catch(function (e) {\n"
    "        var out = document.getElementById('out');\n"
    "        out.style.color = 'var(--norm-error)';\n"
    "        out.textContent = e.message;\n"
    "      });\n"
)

SWAPS = [
    ("icon field", ICON_OLD, ICON_NEW),
    ("style rules", STYLE_OLD, STYLE_NEW),
    ("failure line", FAILURE_OLD, FAILURE_NEW),
    ("skeleton root", ROOT_OLD, ROOT_NEW),
    ("skeleton catch", CATCH_OLD, CATCH_NEW),
]

SAVE_APP_ICON_OLD = "one emoji"
SAVE_APP_ICON_NEW = "a line-icon name from the build_an_app list, e.g. chart-line"


def patch_instructions(text: str) -> tuple[str, list[str]]:
    """The skill text with every swap applied, and what changed.

    Raises SystemExit naming the swap when neither its old nor its new text is
    present — someone edited that line by hand, so a person should look.
    """
    changes: list[str] = []
    for label, old, new in SWAPS:
        if new in text:
            continue
        if text.count(old) != 1:
            raise SystemExit(
                f"build_an_app: {label} not found as expected — edit by hand"
            )
        text = text.replace(old, new, 1)
        changes.append(label)
    return text, changes


def plan(db, dry_run: bool) -> list[str]:
    from sqlalchemy.orm.attributes import flag_modified

    from app.db.config_models import ConnectionSpec, Playbook

    out: list[str] = []

    pb = db.query(Playbook).filter(Playbook.slug == "build_an_app").first()
    if pb is None:
        raise SystemExit("no build_an_app skill — run sync_unified_prompt.py first")
    old_text = pb.instructions or ""
    new_text, changes = patch_instructions(old_text)
    if changes:
        out.append("skill build_an_app: " + ", ".join(changes))
        if dry_run:
            diff = difflib.unified_diff(
                old_text.splitlines(),
                new_text.splitlines(),
                "build_an_app (live)",
                "build_an_app (new)",
                lineterm="",
            )
            out.extend("    " + line for line in diff)
        else:
            pb.instructions = new_text

    spec = (
        db.query(ConnectionSpec).filter(ConnectionSpec.connector_name == "norm").first()
    )
    if spec is None:
        raise SystemExit("no norm spec in the config DB")
    tools = [dict(t) for t in spec.tools or []]
    i = next((n for n, t in enumerate(tools) if t.get("action") == "save_app"), None)
    if i is None:
        raise SystemExit("no norm.save_app tool — run sync_app_builder_agent.py first")
    fields = dict(tools[i].get("field_descriptions") or {})
    if fields.get("icon") != SAVE_APP_ICON_NEW:
        if fields.get("icon") != SAVE_APP_ICON_OLD:
            raise SystemExit(
                f"norm.save_app icon description is {fields.get('icon')!r} — "
                "edit by hand"
            )
        out.append(
            f"tool norm.save_app icon: {SAVE_APP_ICON_OLD!r} -> {SAVE_APP_ICON_NEW!r}"
        )
        if not dry_run:
            tools[i] = {
                **tools[i],
                "field_descriptions": {**fields, "icon": SAVE_APP_ICON_NEW},
            }
            spec.tools = tools
            flag_modified(spec, "tools")
    return out


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    from app.db.engine import _ConfigSessionLocal

    db = _ConfigSessionLocal()
    try:
        if args.dry_run:
            # The shared config DB is live everywhere: a dry run can't write.
            from sqlalchemy import text

            db.execute(text("SET TRANSACTION READ ONLY"))
        changes = plan(db, args.dry_run)
        if not changes:
            print("App Builder style config up to date")
            return
        for c in changes:
            print(f"  {c}")
        if args.dry_run:
            print("(dry run — nothing written)")
            db.rollback()
            return
        db.commit()
        print("committed")
    finally:
        db.close()


if __name__ == "__main__":
    main()
