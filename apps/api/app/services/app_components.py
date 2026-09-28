"""What an App-platform app lets Norm open: its components (28 Sep 2026).

An App-platform app (Norm Hiring, Norm Training, anything App Builder makes)
is one sandboxed page. Its **components** are the screens Norm may open
directly — in chat, from the menu, from a link. By default an app is ONE
component; navigation inside it (tabs, drill-downs, dialogs) is the app's own
business and is never declared.

What makes a component openable somewhere specific is its **inputs**: all
optional, each saying where to start. Norm Hiring's one component takes
``job`` and ``candidate`` — by id or by name, the app resolves them itself — so
"show me the chef role's pipeline" opens Hiring on that pipeline without the
app being split up. Split an app into several components only when a piece is
worth opening on its own in a different shape, is reused elsewhere, or needs
different permissions.

Declared in the version spec:

    "components": [
      {"key": "hiring", "label": "Hiring", "description": "...", "page": true,
       "inputs": {"job": "A job opening — id or title",
                  "candidate": "A candidate — id or name"}}
    ]

An app that declares nothing is one component named after the app, with no
inputs — so every app has components, including the ones saved before this.
"""

from __future__ import annotations

import re

_KEY = re.compile(r"^[a-z][a-z0-9_]{0,39}$")
_INPUT = re.compile(r"^[a-z][a-z0-9_]{0,29}$")


def _inputs(raw) -> list[dict]:
    """``{"job": "desc"}`` or ``{"job": {"description": "desc"}}`` -> a list."""
    out = []
    for name, spec in (raw or {}).items() if isinstance(raw, dict) else []:
        desc = spec.get("description") if isinstance(spec, dict) else spec
        out.append({"name": str(name), "description": str(desc or "")})
    return out


def declared_components(
    spec: dict | None, *, slug: str, name: str, description: str | None = None
) -> list[dict]:
    """The app's components, normalised; the implicit one if it declares none."""
    rows = [
        c
        for c in ((spec or {}).get("components") or [])
        if isinstance(c, dict) and c.get("key")
    ]
    if not rows:
        return [
            {
                "key": re.sub(r"[^a-z0-9_]+", "_", slug.lower()).strip("_") or "app",
                "label": name,
                "description": description or "",
                "page": True,
                "inputs": [],
            }
        ]
    return [
        {
            "key": str(c["key"]),
            "label": str(c.get("label") or c["key"]).strip(),
            "description": str(c.get("description") or ""),
            "page": bool(c.get("page", len(rows) == 1)),
            "inputs": _inputs(c.get("inputs")),
        }
        for c in rows
    ]


def validate_components(spec: dict | None) -> list[str]:
    """Problems with a spec's ``components`` declaration (empty = fine)."""
    raw = (spec or {}).get("components")
    if raw in (None, []):
        return []
    if not isinstance(raw, list):
        return ["spec.components must be a list"]
    problems: list[str] = []
    seen: set[str] = set()
    for i, c in enumerate(raw):
        where = f"spec.components[{i}]"
        if not isinstance(c, dict):
            problems.append(f"{where} must be an object")
            continue
        key = c.get("key")
        if not isinstance(key, str) or not _KEY.match(key):
            problems.append(f"{where}.key must be snake_case (a-z, 0-9, _)")
        elif key in seen:
            problems.append(f"{where}.key '{key}' is declared twice")
        else:
            seen.add(key)
        inputs = c.get("inputs")
        if inputs is not None and not isinstance(inputs, dict):
            problems.append(f"{where}.inputs must be an object of name -> description")
        for name in inputs or {} if isinstance(inputs, dict) else []:
            if not _INPUT.match(str(name)):
                problems.append(f"{where}.inputs '{name}' must be snake_case")
    return problems


def pick_component(components: list[dict], key: str | None) -> dict | None:
    """The component asked for — or the only one when none is named."""
    if key:
        return next((c for c in components if c["key"] == key), None)
    return components[0] if len(components) == 1 else None


def check_inputs(component: dict, inputs: dict | None) -> tuple[dict, list[str]]:
    """Keep the declared inputs that carry a value; name any undeclared ones."""
    allowed = {i["name"] for i in component["inputs"]}
    clean: dict = {}
    unknown: list[str] = []
    for k, v in (inputs or {}).items():
        if k not in allowed:
            unknown.append(k)
        elif v not in (None, ""):
            clean[k] = v
    return clean, unknown
