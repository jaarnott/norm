"""Install the "pages people can edit by hand" rules on the build_an_app skill.

People can now change an app's page themselves: Edit mode on the app's page
retypes any words and drags sections, and writes the change into the app's
code (web lib/codeEdits.ts, components/apps/editRuntime.ts). For that to work,
Norm has to build pages laid out in rows, columns and sections, keep visible
words as plain strings, and start every change from the code as the person
left it. Plan: docs/plans/apps-and-dashboards-one-system.md.

One config edit, backed up first: the section below goes at the top of the
build_an_app skill (once; a changed text replaces the installed one).

**Run AFTER the code with Edit mode is deployed.** The config DB is shared by
every environment, so production reads it straight away.

Usage (from apps/api, .env loaded):
    uv run python scripts/sync_app_editing_skill.py --dry-run
    uv run python scripts/sync_app_editing_skill.py
"""

from __future__ import annotations

import argparse
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from scripts.tool_row_updates import BACKUP_DIR, apply_row_updates  # noqa: E402

MARKER = "## Pages people can edit by hand"
SECTION = """## Pages people can edit by hand
People can change an app's page themselves. In Edit mode they click any words to retype them, and drag sections to rearrange them. What they change is written into your code, so `get_app` gives you the code as they left it (its `last_change` says "Edited by hand: …"). Build every page so this works:

- **Lay the page out in rows, columns and sections, in the markup:**
  ```html
  <div data-norm-row>
    <div data-norm-col data-norm-span="2">
      <section class="n-card" data-norm-block="sales"></section>
      <section class="n-card" data-norm-block="sales-chart"></section>
    </div>
    <div data-norm-col>
      <section class="n-card" data-norm-block="wages"></section>
    </div>
  </div>
  ```
  A row is one line: its columns sit side by side and never wrap, and they stack on a phone. For a grid (four figures in two pairs), give each line its own row. `data-norm-span` is a column's share of its row (1 to 12); never set column widths in your own CSS, or a person's width can't be kept. Each section is one card: a table, a chart, a set of figures. Give each section a short id that says what it holds. Controls for the whole page (period tabs, a switch) go above the first row.
- **Every section must work at any width,** from a phone to the full page, because people resize columns. Let a wide table scroll sideways inside its own wrapper (`overflow-x: auto`) rather than spill out of its card.
- **Keep the layout in the markup.** Fill each section from the script: `norm.block('sales').innerHTML = …`. Never rebuild the page or its rows, columns and sections in the script, or a person's moves can't be kept.
- **Write every word people see as a plain string in the code, exactly as it shows:** `'Wages and hours'`. Don't build it from pieces, and don't change its case in the script (use CSS `text-transform` instead). Then people can retype it.
- **Start every change from the code as it is now.** Keep what the person changed by hand (their words, the order of sections, column widths) unless they ask you to change it.

"""


def also(db, dry_run: bool) -> list[str]:
    import datetime as dt
    import json

    from app.db.config_models import Playbook

    skill = db.query(Playbook).filter(Playbook.slug == "build_an_app").one()
    text = skill.instructions or ""
    if SECTION.strip() in text:
        return []
    if MARKER in text:
        # Replace the installed section (it runs to the next top heading).
        head, _, rest = text.partition(MARKER)
        _, nxt, tail = rest.partition("\n## ")
        text = head + (("## " + tail) if nxt else "")
        line = "build_an_app: update the hand-editing rules"
    else:
        line = "build_an_app: add the hand-editing rules"
    if not dry_run:
        BACKUP_DIR.mkdir(exist_ok=True)
        stamp = dt.datetime.now(dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        path = BACKUP_DIR / f"app-editing-skill-config-{stamp}.json"
        path.write_text(json.dumps({"build_an_app": skill.instructions}, indent=1))
        print(f"backup {path}")
        skill.instructions = SECTION + text.lstrip()
    return [line]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    apply_row_updates([], dry_run=args.dry_run, label="app-editing-skill", also=also)


if __name__ == "__main__":
    main()
