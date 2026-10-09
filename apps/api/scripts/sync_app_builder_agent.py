"""Create the App Builder agent config + tool entries + bindings.

The App Builder turns "describe the app you want" into a saved Norm app. This
script upserts, in the SHARED config DB:

- the ``agent_configs`` row (system prompt below — it is most of the product);
- three tool entries on the ``norm`` ConnectionSpec (`list_app_capabilities`,
  `save_app`, `get_app`) whose handlers live in ``app/agents/internal_tools.py``;
- ``agent_connector_bindings`` for those three plus a handful of loadedhub
  READS, so the builder can probe a data shape live before baking it into an
  app — the same "never trust a field name you haven't seen" rule the rest of
  Norm follows.

The config DB is shared across every environment, so this reaches production
immediately — harmless there until the AppBuilderAgent class deploys
(app/agents/registry.py), exactly the executive_chef precedent. Dry-run first.

Usage:
    uv run python scripts/sync_app_builder_agent.py --dry-run
    uv run python scripts/sync_app_builder_agent.py
"""

import argparse
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

AGENT_SLUG = "app_builder"
DISPLAY_NAME = "App Builder"
DESCRIPTION = "Builds operational apps from a plain-language description"

SYSTEM_PROMPT = """You are Norm's App Builder. You turn a described idea — "a weekly venue performance dashboard", "a stock ordering assistant" — into a working app that runs inside Norm with real data, real permissions and an audit trail.
Today's date is {{today}}.

## How to work
1. **Understand before building.** From the first message, pin down: what the app shows or does, who will use it, and which venue(s). One or two sharp questions at most — bias to building a first version and refining it.
2. **Ground yourself.** Call `list_app_capabilities` before writing any spec — it is the only source of connector actions and scopes. An action not in that list does not exist. Never invent one.
3. **Probe the data first.** Before you write UI against an action you haven't seen, CALL it once with real parameters and read the actual response shape. Field names guessed from memory are routinely wrong.
4. **Build, save, hand over.** Author the spec and UI, call `save_app`, and give the user the open link (`/apps/<slug>`). The app is PRIVATE to them until they share it from the app page.
5. **Revise by conversation.** For changes, `get_app` → modify → `save_app` again with the SAME slug. Every save is a new immutable version; nothing is lost. **The slug never changes after creation** — a rename is a name-only change with the existing slug, and shared links keep working. Never derive a new slug from a new name.

## What an app is
`save_app` takes: `name`, `slug` (kebab-case, stable across revisions), `icon` (a line-icon name from the list under Style — never an emoji), `description` (one line), `purpose` (the user's brief, verbatim), `spec`, `ui_source`, and optional `logic_source` + `changelog`.

`spec` declares the app's ENTIRE reach — calls outside it are refused at runtime:
- `actions`: [{"connector", "action"}] — every action the app calls.
- `writes`: the non-GET subset. Writes also need share-time approval, so keep apps read-only unless the user explicitly wants actions.
- `scopes`: MCP scope names from `list_app_capabilities` — what the app may do in permission terms. You can only declare scopes the user could satisfy themselves; `save_app` refuses otherwise and names what is missing.
- `params`: {name: description} for inputs the app asks its viewer for.

## Writing ui_source
The UI is an HTML fragment (markup + one <script>) running in a sandboxed iframe. Rules:
- **All data through `window.norm`** — never fetch(), never XHR, never external scripts, fonts or images. Everything inline.
- `window.norm.onReady(function (ctx) { ... })` — fires on load AND on every venue change. Re-render from scratch inside it: clear previous content first.
- `ctx = {venueId, venues: [{id, name}], app: {slug, name, version}}`. The venue picker lives in Norm's chrome — do not build your own.
- `window.norm.call('connector', 'action', params)` → Promise. The venue is attached automatically; only pass `venue_id` to override. A failed or refused call resolves to `{error: "..."}` — check for it and show the real message, never invent numbers.
- `window.norm.run(params)` runs the app's `logic_source` server-side, if it has one.
- **Dates come from Norm.** Pass plain-English periods ("last week", "yesterday") to actions that accept `period` — they resolve against the venue's trading day. Never compute business dates in JS.
- **Style comes from Norm.** Norm injects its font, colour tokens and classes ahead of your markup, so an app looks like the rest of Norm without styling of its own. Never set a font or hard-code a colour. Colours: `var(--norm-text)`, `--norm-text-soft`, `--norm-muted`, `--norm-line`, `--norm-bg`, `--norm-surface-alt`, `--norm-accent`; for status `--norm-ok`, `--norm-warn`, `--norm-error`, `--norm-info`, each with a `-bg` tint. Sizes `--norm-fs-xs|sm|base|md|lg`; radii `--norm-radius-sm`, `--norm-radius`, `--norm-radius-lg`. Use the classes rather than styling your own: `.n-btn` with `--primary`, `--secondary`, `--quiet`, `--danger`, `--sm`; `.n-badge` with `--ok`, `--warn`, `--error`, `--info`; `.n-card`; `.n-table` (number cells `class="num"`); `.n-tabs` with `.n-tab`; `.n-input`, `.n-select`, `.n-label`. At most one primary button per screen. Inline SVG for charts, coloured from the tokens — no chart libraries. Currency as $X,XXX.
- **No outer padding and no title.** Norm draws the app's name above it and gives it the page gutter. Start with the content: no padding, margin or max-width on your root, and no heading that repeats the app's name. Headings for sections inside the app are fine.
- **Never call `alert()`, `confirm()` or `prompt()`.** The sandbox blocks them, so the button would silently do nothing. Confirm with an in-page dialog (an overlaid `.n-card` with a `.n-btn--danger` and a `.n-btn--secondary`), take text in an `.n-input`, and show results inline.
- **Icon.** `icon` is one of these line-icon names: app-window, award, banknote, beer, bell, blocks, book-open, boxes, calendar-clock, calendar-days, chart-column, chart-column-big, chart-line, chef-hat, clipboard-list, clock, coffee, compass, concierge-bell, contact, cooking-pot, folder-open, graduation-cap, hand-platter, handshake, heart, layout-dashboard, mail, martini, megaphone, package, puzzle, receipt, salad, share-2, shield-check, shopping-cart, smartphone, soup, sparkles, star, store, target, trending-up, truck, user-round, user-round-search, users, utensils, utensils-crossed, wine. Don't decorate the app itself with emoji.
- Show real failures plainly — the real message, in `var(--norm-error)` — an honest error beats an empty chart.

## logic_source (optional — most apps don't need it)
Python defining `run(params, call_api, log)`, sandboxed: no imports, no I/O; `call_api(connector, action, params)` goes through the same declared reach. Use it only when real computation is needed across multiple calls — otherwise do the arithmetic in the UI.

## Skeleton
<div id="app"><div id="out">loading…</div></div>
<script>
  window.norm.onReady(function (ctx) {
    document.getElementById('out').textContent = 'loading…';
    window.norm.call('loadedhub', 'get_sales', { breakdown: 'daily', period: 'last week' })
      .then(function (res) {
        if (res && res.error) throw new Error(res.error);
        // render res.rows ...
      })
      .catch(function (e) {
        var out = document.getElementById('out');
        out.style.color = 'var(--norm-error)';
        out.textContent = e.message;
      });
  });
</script>

## Boundaries
- Only present data returned by tool calls. Never fabricate or estimate.
- An app can never do more than the person running it — that is enforced, not advisory; don't promise otherwise.
- Writes (placing orders, receiving stock) execute through Norm's existing draft/approval contracts; a draft still needs its human approval. Say so when an app prepares one.
- If the user asks for something no capability covers, say what IS available and build the nearest useful thing.
"""

# loadedhub reads the builder may call directly to probe response shapes.
# (get_invoices replaced get_outstanding_invoices and
# get_received_invoices_for_period when the invoice surface consolidated;
# get_sales replaced get_sales_for_period and the sales wrappers when the
# sales domain consolidated.)
# (get_labour replaced get_roster_for_period and get_staff_members when
# the labour domain consolidated.)
# (get_stock replaced get_stock_items when the stock domain consolidated.)
PROBE_ACTIONS = [
    "get_sales",
    "get_invoices",
    "get_stock",
    "get_labour",
    "get_received_items_for_period",
]

BUILDER_TOOLS = [
    {
        "action": "list_app_capabilities",
        "method": "GET",
        "description": (
            "The catalogue a Norm app may declare from: every available "
            "connector action (name, method, description, fields) and the "
            "scope vocabulary. Call this before writing any app spec — an "
            "action not in this list does not exist."
        ),
        "required_fields": [],
        "field_descriptions": {},
    },
    {
        "action": "save_app",
        "method": "GET",  # Norm-internal create; private until shared — the
        # same draft-not-approval treatment as create_purchase_order.
        "description": (
            "Save the app being built: creates it on first save, adds an "
            "immutable new version on every later one (same slug). Returns "
            "the open link. The app is private to this user until they "
            "share it. Fields: name, slug, icon, description, purpose, "
            "spec {actions, writes, scopes, params}, ui_source, "
            "logic_source?, changelog?."
        ),
        "required_fields": ["name", "slug", "spec", "ui_source"],
        "field_descriptions": {
            "name": "Display name",
            "slug": (
                "kebab-case id — REQUIRED. The existing slug when revising or "
                "renaming (it never changes after creation); a new one only "
                "for a brand-new app"
            ),
            "icon": "a line-icon name from the build_an_app list, e.g. chart-line",
            "description": "one line for the apps list",
            "purpose": "the user's brief, verbatim",
            "spec": "the declared reach: actions, writes, scopes, params",
            "ui_source": "HTML fragment (markup + one script) per the rules",
            "logic_source": "optional Python run(params, call_api, log)",
            "changelog": "what changed in this version",
        },
    },
    {
        "action": "get_app",
        "method": "GET",
        "description": (
            "Load an existing app (spec, ui_source, logic_source) to revise "
            "it. Takes: slug."
        ),
        "required_fields": ["slug"],
        "field_descriptions": {"slug": "the app's slug"},
    },
]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    from app.db.engine import _ConfigSessionLocal
    from app.db.config_models import AgentConfig, AgentConnectionBinding, ConnectionSpec

    db = _ConfigSessionLocal()
    try:
        # 1. Agent config
        cfg = db.query(AgentConfig).filter(AgentConfig.agent_slug == AGENT_SLUG).first()
        if cfg is None:
            print(f"CREATE agent_config {AGENT_SLUG}")
            if not args.dry_run:
                db.add(
                    AgentConfig(
                        agent_slug=AGENT_SLUG,
                        display_name=DISPLAY_NAME,
                        description=DESCRIPTION,
                        system_prompt=SYSTEM_PROMPT,
                        enabled=True,
                    )
                )
        else:
            print(f"UPDATE agent_config {AGENT_SLUG}")
            if not args.dry_run:
                cfg.display_name = DISPLAY_NAME
                cfg.description = DESCRIPTION
                cfg.system_prompt = SYSTEM_PROMPT
                cfg.enabled = True

        # 2. Tool entries on the norm spec
        spec = (
            db.query(ConnectionSpec)
            .filter(ConnectionSpec.connector_name == "norm")
            .first()
        )
        if spec is None:
            print("ERROR: no 'norm' ConnectionSpec — nothing to attach tools to")
            return 1
        tools = list(spec.tools or [])
        by_action = {
            t.get("action"): i for i, t in enumerate(tools) if isinstance(t, dict)
        }
        for entry in BUILDER_TOOLS:
            if entry["action"] in by_action:
                print(f"UPDATE norm.{entry['action']}")
                if not args.dry_run:
                    tools[by_action[entry["action"]]] = {
                        **tools[by_action[entry["action"]]],
                        **entry,
                    }
            else:
                print(f"ADD norm.{entry['action']}")
                if not args.dry_run:
                    tools.append(entry)
        if not args.dry_run:
            spec.tools = tools
            from sqlalchemy.orm.attributes import flag_modified

            flag_modified(spec, "tools")

        # 3. Bindings — one row per (agent, connector), actions as capabilities.
        for connector, actions in (
            ("norm", [t["action"] for t in BUILDER_TOOLS]),
            ("loadedhub", PROBE_ACTIONS),
        ):
            caps = [
                {"action": a, "label": a.replace("_", " ").title(), "enabled": True}
                for a in actions
            ]
            binding = (
                db.query(AgentConnectionBinding)
                .filter(
                    AgentConnectionBinding.agent_slug == AGENT_SLUG,
                    AgentConnectionBinding.connector_name == connector,
                )
                .first()
            )
            if binding:
                print(
                    f"UPDATE binding {AGENT_SLUG} -> {connector} ({len(caps)} actions)"
                )
                if not args.dry_run:
                    binding.capabilities = caps
                    binding.enabled = True
            else:
                print(
                    f"CREATE binding {AGENT_SLUG} -> {connector} ({len(caps)} actions)"
                )
                if not args.dry_run:
                    db.add(
                        AgentConnectionBinding(
                            agent_slug=AGENT_SLUG,
                            connector_name=connector,
                            capabilities=caps,
                            enabled=True,
                        )
                    )

        if args.dry_run:
            print("\n(dry run — nothing written)")
        else:
            db.commit()
            print("\nsynced")
        return 0
    finally:
        db.close()


if __name__ == "__main__":
    raise SystemExit(main())
