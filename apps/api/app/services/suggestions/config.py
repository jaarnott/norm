"""Home's suggestions, tuned site-wide from the config DB (Oct 2026).

The defaults live in code: the catalogue prompts in ``catalog.py``, the insight
detectors in ``insights/rules.py``, and the global settings below. The shared
config DB's ``suggestion_config`` table holds only what a platform admin
changed on Settings → AI team → Suggestions (docs/plans/suggestions-config.md):
``settings``, ``prompt:<id>`` and ``detector:<name>`` rows, each a sparse
override. An empty table is exactly the defaults.

Everything reads ``current(config_db)``. It is cached for a minute, because Home
asks on every visit and the config DB is shared and capacity-limited. A value
that fails its check is dropped back to its default with a warning, so a bad
row can never empty Home. Saves go through ``save_*``/``reset``/``undo``, which
check first and refuse with reasons a person can read.
"""

from __future__ import annotations

import copy
import logging
import re
import time
from dataclasses import asdict, dataclass, field

from app.services.insights import rules as insight_rules
from app.services.suggestions.catalog import CATALOG, Entry

logger = logging.getLogger(__name__)

CACHE_SECONDS = 60
ABSENT = {"_absent": True}


@dataclass(frozen=True)
class Setting:
    default: object
    label: str
    help: str
    #: bool | count | number | times | percent (a fraction) | days | text
    kind: str
    minimum: float = 0
    maximum: float = 1_000_000
    group: str = "scoring"


def _wording_default() -> str:
    from app.services.insights.wording import SYSTEM

    return SYSTEM


def _guidance_default() -> str:
    from app.services.insights.context import GUIDANCE

    return GUIDANCE


#: The insight-chat guidance's placeholders.
GUIDANCE_PLACEHOLDERS = {
    "insight_id": "the insight's id (Norm passes it to note_insight)",
    "venue": "the venue's name",
    "as_of": "the date its numbers run to",
    "headline": "the insight's headline",
    "notes": "what people already said about it (a short list), or nothing",
}

SETTINGS: dict[str, Setting] = {
    # ---- switches
    "insights_enabled": Setting(
        True,
        "Insights",
        "Run the daily insight check and show insights. Off hides them everywhere.",
        "bool",
        group="switches",
    ),
    "wording_enabled": Setting(
        True,
        "Reword insights",
        "Haiku rewords each insight's prompt in the organisation's own words, at "
        "Norm's cost (about $0.004 a venue a run). Off keeps the templates' words.",
        "bool",
        group="switches",
    ),
    # ---- slots
    "insight_slots_desktop": Setting(
        3,
        "Insights on a desktop",
        "Of the 4 slots a desktop shows.",
        "count",
        0,
        4,
        "slots",
    ),
    "insight_slots_phone": Setting(
        2,
        "Insights on a phone",
        "Of the 2 slots a phone shows.",
        "count",
        0,
        2,
        "slots",
    ),
    "area_diversity": Setting(
        True,
        "Insights from different areas",
        "Prefer one insight each from sales, wages and cost of goods over two from one area.",
        "bool",
        group="slots",
    ),
    "pending_slot": Setting(
        True,
        "A waiting-on-you slot",
        "Invoices that need a person take a slot when there are some.",
        "bool",
        group="slots",
    ),
    # ---- scoring
    "venue_boost": Setting(
        1.5,
        "The venue they're in, first",
        "Added to an insight at the venue the person is working in.",
        "number",
        0,
        10,
    ),
    "regular_boost": Setting(
        1.6,
        "Prompts they ask often",
        "Times a catalogue prompt's weight when they've used its tools in the last 30 days.",
        "times",
        0,
        5,
    ),
    "discover_boost": Setting(
        1.4,
        "Something new to try",
        "Times a discover prompt's weight when they've never used its tools.",
        "times",
        0,
        5,
    ),
    "timely_bonus": Setting(
        0.4,
        "Timely",
        "Added when a prompt's day or time of day is now.",
        "number",
        0,
        5,
    ),
    "clicked_bonus": Setting(
        0.2, "Clicked before", "Added when they've clicked it before.", "number", 0, 5
    ),
    "ignored_after": Setting(
        3,
        "Ignored after",
        "Shown this many times in 14 days without a click …",
        "count",
        1,
        50,
    ),
    "ignored_factor": Setting(
        0.5, "… then times", "… and its score is multiplied by this.", "times", 0, 1
    ),
    # ---- memory
    "repeat_days": Setting(
        7,
        "Show an insight again after",
        "An insight is shown to a person at most once in this many days …",
        "days",
        1,
        90,
        "memory",
    ),
    "repeat_move": Setting(
        0.25,
        "… or sooner if its value moves",
        "… unless its value has moved by this much since.",
        "percent",
        0,
        10,
        "memory",
    ),
    "dismiss_days": Setting(
        30,
        "A dismissed suggestion stays away",
        "After the × on Home.",
        "days",
        1,
        365,
        "memory",
    ),
    "expected_days": Setting(
        60,
        "“Expected” keeps an insight quiet for",
        "When someone explained it and gave no end date.",
        "days",
        1,
        365,
        "memory",
    ),
    "expected_growth": Setting(
        1.5,
        "… unless it grows to",
        "Times its value when it was explained.",
        "times",
        1,
        20,
        "memory",
    ),
    "acting_days": Setting(
        28,
        "“Acting” keeps an insight quiet for",
        "When someone said they'd act on it and gave no end date.",
        "days",
        1,
        365,
        "memory",
    ),
    # ---- words
    "wording_instructions": Setting(
        None,  # wording.SYSTEM, read lazily
        "Wording instructions",
        "What Haiku is told when it rewords insights. Saving them rewords every live "
        "insight on its organisation's next run.",
        "text",
        group="wording",
    ),
    "insight_guidance": Setting(
        None,  # context.GUIDANCE, read lazily
        "When a chat starts from an insight",
        "Added to Norm's instructions in that chat: when to record what people say with "
        "note_insight.",
        "text",
        group="wording",
    ),
}

PROMPT_KINDS = ("regular", "discover")
PARTS = ("morning", "afternoon", "evening")
_ID = re.compile(r"^[a-z0-9_]+\.[a-z0-9_]+$")
_TOOL = re.compile(r"^[a-z0-9_]+\.[a-z0-9_]+$")


def setting_default(name: str):
    if name == "wording_instructions":
        return _wording_default()
    if name == "insight_guidance":
        return _guidance_default()
    return SETTINGS[name].default


def default_settings() -> dict:
    return {k: setting_default(k) for k in SETTINGS} | {"wording_version": 0}


def default_prompts() -> dict[str, dict]:
    out = {}
    for e in CATALOG:
        d = asdict(e)
        d["tools"] = list(e.tools)
        d["days"] = list(e.days) if e.days is not None else None
        d["enabled"] = True
        out[e.id] = d
    return out


# ------------------------------------------------------------------ checks
class Invalid(ValueError):
    def __init__(self, problems: list[str]):
        super().__init__("; ".join(problems))
        self.problems = problems


def _number(spec_min, spec_max, value, label) -> list[str]:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return [f"{label} must be a number."]
    if not spec_min <= value <= spec_max:
        return [f"{label} must be between {spec_min:g} and {spec_max:g}."]
    return []


def check_setting(name: str, value) -> list[str]:
    spec = SETTINGS.get(name)
    if spec is None:
        return [f"There is no setting called {name}."]
    if spec.kind == "bool":
        return [] if isinstance(value, bool) else [f"{spec.label} must be on or off."]
    if spec.kind == "text":
        if not isinstance(value, str) or not value.strip():
            return [f"{spec.label} can't be empty."]
        if len(value) > 12000:
            return [f"{spec.label} is over 12,000 characters."]
        if name == "insight_guidance":
            return insight_rules.check_template(value, GUIDANCE_PLACEHOLDERS)
        return []
    problems = _number(spec.minimum, spec.maximum, value, spec.label)
    if not problems and spec.kind in ("count", "days") and float(value) != int(value):
        problems.append(f"{spec.label} must be a whole number.")
    return problems


def check_prompt(
    p: dict, members: set[str] | None = None, apps: set[str] | None = None
) -> list[str]:
    """Problems with a whole (merged) prompt. ``members``/``apps`` are checked
    when given (on save; the reader can't afford the lookups)."""
    from app.auth.permissions import ALL_ORG_PERMISSIONS

    out = []
    text = p.get("text")
    if not isinstance(text, str) or not text.strip():
        out.append("The prompt is empty.")
    elif len(text) > 300:
        out.append("The prompt is over 300 characters.")
    else:
        out += insight_rules.check_template(text, {"venue": "the person's venue"})
    if members is not None and p.get("member") not in members:
        out.append(f"{p.get('member')!r} isn't a team member.")
    if apps is not None and p.get("app") not in apps:
        out.append(f"{p.get('app')!r} isn't an App.")
    tools = p.get("tools")
    if not isinstance(tools, list) or not all(
        isinstance(t, str) and _TOOL.match(t) for t in tools
    ):
        out.append("Tools are written connector.action, e.g. loadedhub.get_sales.")
    if p.get("kind") not in PROMPT_KINDS:
        out.append("Kind is regular or discover.")
    perm = p.get("permission")
    if perm is not None and perm not in ALL_ORG_PERMISSIONS:
        out.append(f"{perm!r} isn't a permission.")
    conn = p.get("connection")
    if conn is not None and not (isinstance(conn, str) and conn.strip()):
        out.append("Connection is a connector name, or none.")
    days = p.get("days")
    if days is not None and not (
        isinstance(days, list)
        and days
        and all(isinstance(d, int) and 0 <= d <= 6 for d in days)
    ):
        out.append("Days are weekdays 0 (Monday) to 6 (Sunday), or none.")
    if p.get("part") is not None and p.get("part") not in PARTS:
        out.append("Time of day is morning, afternoon or evening, or none.")
    for flag in ("month_start", "multi_venue", "enabled"):
        if not isinstance(p.get(flag), bool):
            out.append(f"{flag} must be on or off.")
    out += _number(0, 5, p.get("weight"), "Weight")
    return out


def check_detector(name: str, o: dict, members: set[str] | None = None) -> list[str]:
    """Problems with a detector override (only the fields it sets)."""
    spec = insight_rules.DETECTORS.get(name)
    if spec is None:
        return [f"There is no detector called {name}."]
    out = []
    if "enabled" in o and not isinstance(o["enabled"], bool):
        out.append("Enabled must be on or off.")
    for k in ("weight", "lift"):
        if k in o:
            out += _number(0, 5, o[k], k.title())
    if "members" in o:
        m = o["members"]
        if not isinstance(m, list) or not all(isinstance(x, str) for x in m):
            out.append("Members is a list of team members.")
        elif members is not None and any(x not in members for x in m):
            out.append(
                "Unknown team member: " + ", ".join(x for x in m if x not in members)
            )
    for k, v in (o.get("thresholds") or {}).items():
        t = spec.thresholds.get(k)
        if t is None:
            out.append(f"{spec.label} has no threshold {k!r}.")
        else:
            out += _number(t.minimum, t.maximum, v, t.label)
    for k, v in (o.get("templates") or {}).items():
        t = spec.templates.get(k)
        if t is None:
            out.append(f"{spec.label} has no template {k!r}.")
        else:
            out += insight_rules.check_template(v, t.placeholders)
    return out


# ------------------------------------------------------------------ the effective config
@dataclass
class Effective:
    settings: dict
    prompts: list[Entry]
    #: Every prompt, for the admin page: default and custom, on and off.
    prompt_rows: dict[str, dict]
    #: name -> enabled, weight, lift, members, thresholds, templates
    detectors: dict[str, dict]
    #: The detectors' overrides as insights/rules.py reads them (VenueData.rules).
    rules: dict = field(default_factory=dict)
    overrides: dict = field(default_factory=dict)

    def detector(self, name: str) -> dict:
        d = self.detectors.get(name)
        if d is not None:
            return d
        # A finding type with no spec (an old row) — on, by area.
        return {"enabled": True, "weight": 1.0, "lift": 0.5, "members": None}

    def members_for(self, detector: str, area: str) -> tuple[str, ...]:
        m = self.detector(detector).get("members")
        return tuple(m) if m is not None else insight_rules.AREA_MEMBERS.get(area, ())

    @property
    def wording_version(self) -> str:
        from app.services.insights.wording import VERSION

        n = int(self.settings.get("wording_version") or 0)
        return f"{VERSION}.{n}" if n else VERSION


def build(rows: dict[str, dict]) -> Effective:
    """The effective config from override rows (key -> value)."""
    settings = default_settings()
    for k, v in (rows.get("settings") or {}).items():
        if k == "wording_version":
            if isinstance(v, int) and not isinstance(v, bool) and v >= 0:
                settings[k] = v
            continue
        if check_setting(k, v):
            logger.warning("suggestion setting %s ignored: invalid", k)
            continue
        settings[k] = v

    defaults = default_prompts()
    prompt_rows: dict[str, dict] = {}
    for pid, d in defaults.items():
        prompt_rows[pid] = {**d, "custom": False}
    for key, o in rows.items():
        if not key.startswith("prompt:") or not isinstance(o, dict):
            continue
        pid = key.split(":", 1)[1]
        if pid in defaults:
            merged = {
                **defaults[pid],
                **{k: v for k, v in o.items() if k in defaults[pid] and k != "id"},
            }
            if check_prompt(merged):
                logger.warning("suggestion prompt %s override ignored: invalid", pid)
                continue
            prompt_rows[pid] = {**merged, "custom": False}
        else:
            if o.get("deleted"):
                continue
            merged = {**defaults_template(), **o, "id": pid}
            if check_prompt(merged):
                logger.warning("custom suggestion prompt %s ignored: invalid", pid)
                continue
            prompt_rows[pid] = {**merged, "custom": True}
    prompts = [
        Entry(
            id=p["id"],
            member=p["member"],
            app=p["app"],
            text=p["text"],
            tools=tuple(p["tools"]),
            kind=p["kind"],
            permission=p["permission"],
            connection=p["connection"],
            days=tuple(p["days"]) if p["days"] is not None else None,
            part=p["part"],
            month_start=p["month_start"],
            multi_venue=p["multi_venue"],
            weight=float(p["weight"]),
        )
        for p in prompt_rows.values()
        if p["enabled"]
    ]

    detectors: dict[str, dict] = {}
    rules: dict[str, dict] = {}
    for name, spec in insight_rules.DETECTORS.items():
        o = rows.get(f"detector:{name}") or {}
        if not isinstance(o, dict):
            o = {}
        eff = {
            "enabled": True,
            "weight": spec.weight,
            "lift": spec.lift,
            "members": list(spec.members) if spec.members is not None else None,
            "thresholds": {k: t.default for k, t in spec.thresholds.items()},
            "templates": {k: t.default for k, t in spec.templates.items()},
        }
        mine: dict = {"thresholds": {}, "templates": {}}
        for k in ("enabled", "weight", "lift", "members"):
            if k in o and not check_detector(name, {k: o[k]}):
                eff[k] = o[k]
        for k, v in (o.get("thresholds") or {}).items():
            if not check_detector(name, {"thresholds": {k: v}}):
                eff["thresholds"][k] = v
                mine["thresholds"][k] = v
        for k, v in (o.get("templates") or {}).items():
            if not check_detector(name, {"templates": {k: v}}):
                eff["templates"][k] = v
                mine["templates"][k] = v
        detectors[name] = eff
        if mine["thresholds"] or mine["templates"]:
            rules[name] = mine
    return Effective(settings, prompts, prompt_rows, detectors, rules, rows)


def defaults_template() -> dict:
    """The fields a custom prompt has, with a new prompt's defaults."""
    return {
        "id": None,
        "member": None,
        "app": None,
        "text": "",
        "tools": [],
        "kind": "regular",
        "permission": None,
        "connection": "loadedhub",
        "days": None,
        "part": None,
        "month_start": False,
        "multi_venue": False,
        "weight": 1.0,
        "enabled": True,
    }


_cache: dict = {"at": 0.0, "value": None}


def clear_cache() -> None:
    _cache["at"], _cache["value"] = 0.0, None


def load_rows(config_db) -> dict[str, dict]:
    from app.db.config_models import SuggestionConfig

    return {
        r.key: copy.deepcopy(r.value or {})
        for r in config_db.query(SuggestionConfig).all()
    }


def current(config_db=None) -> Effective:
    """The effective config, at most a minute old. Without a session (or if
    the table can't be read) the defaults, so suggestions never fail on it."""
    now = time.monotonic()
    if _cache["value"] is not None and now - _cache["at"] < CACHE_SECONDS:
        return _cache["value"]
    if config_db is None:
        return build({})
    try:
        value = build(load_rows(config_db))
    except Exception:  # noqa: BLE001 — the defaults stand
        logger.exception("suggestion config unreadable; using the defaults")
        try:
            config_db.rollback()
        except Exception:  # noqa: BLE001
            pass
        value = build({})
    _cache["at"], _cache["value"] = now, value
    return value


# ------------------------------------------------------------------ saving
def known_members(config_db) -> set[str]:
    """The team (agent_configs), and every member a built-in prompt or
    detector already names."""
    from app.db.config_models import AgentConfig

    builtin = {e.member for e in CATALOG} | {
        m for ms in insight_rules.AREA_MEMBERS.values() for m in ms
    }
    team = {a.agent_slug for a in config_db.query(AgentConfig).all()}
    return team | builtin | {"norm"}


def known_apps(config_db) -> set[str]:
    """The Apps (marketplace_apps), and every App a built-in prompt names."""
    from app.db.config_models import MarketplaceApp

    apps = {a.slug for a in config_db.query(MarketplaceApp).all()}
    return apps | {e.app for e in CATALOG} | {"norm-core"}


def _write(config_db, key: str, value: dict, by: str | None):
    from app.db.config_models import SuggestionConfig

    row = config_db.query(SuggestionConfig).filter(SuggestionConfig.key == key).first()
    old = copy.deepcopy(row.value) if row is not None else None
    if old == value:
        return row
    if row is None:
        row = SuggestionConfig(key=key, value=value, previous=ABSENT, updated_by=by)
        config_db.add(row)
    else:
        row.previous = old
        row.value = value
        row.updated_by = by
    config_db.commit()
    clear_cache()
    return row


def _row_value(config_db, key: str) -> dict:
    from app.db.config_models import SuggestionConfig

    row = config_db.query(SuggestionConfig).filter(SuggestionConfig.key == key).first()
    return (
        copy.deepcopy(row.value)
        if row is not None and isinstance(row.value, dict)
        else {}
    )


def save_settings(config_db, changes: dict, by: str | None) -> dict:
    """``changes``: name -> value, or None to put it back to its default."""
    value = _row_value(config_db, "settings")
    problems = []
    for k, v in changes.items():
        if k == "wording_version" or k not in SETTINGS:
            problems.append(f"There is no setting called {k}.")
            continue
        if v is None or v == setting_default(k):
            if k in value:
                value.pop(k)
                if k == "wording_instructions":
                    value["wording_version"] = (
                        int(value.get("wording_version") or 0) + 1
                    )
            continue
        problems += check_setting(k, v)
        if k == "wording_instructions" and value.get(k) != v:
            value["wording_version"] = int(value.get("wording_version") or 0) + 1
        value[k] = v
    if problems:
        raise Invalid(problems)
    _write(config_db, "settings", value, by)
    return value


def save_prompt(config_db, prompt_id: str, changes: dict, by: str | None) -> dict:
    """Change a default prompt (a field set to None goes back to its default)
    or create/change a custom one (stored whole)."""
    defaults = default_prompts()
    key = f"prompt:{prompt_id}"
    members, apps = known_members(config_db), known_apps(config_db)
    if prompt_id in defaults:
        d = defaults[prompt_id]
        value = _row_value(config_db, key)
        for k, v in changes.items():
            if k not in d or k == "id":
                raise Invalid([f"A prompt has no field {k!r}."])
            if v is None and k not in ("permission", "connection", "days", "part"):
                value.pop(k, None)
            elif v == d[k]:
                value.pop(k, None)
            else:
                value[k] = v
        problems = check_prompt({**d, **value}, members, apps)
    else:
        if not _ID.match(prompt_id):
            raise Invalid(
                ["A prompt's id is member.name, lower case, e.g. reports.weekly_wages."]
            )
        base = _row_value(config_db, key) or defaults_template()
        base.pop("deleted", None)
        value = {
            **base,
            **{k: v for k, v in changes.items() if k in defaults_template()},
        }
        unknown = [k for k in changes if k not in defaults_template()]
        if unknown:
            raise Invalid([f"A prompt has no field {k!r}." for k in unknown])
        value["id"] = prompt_id
        problems = check_prompt(value, members, apps)
    if problems:
        raise Invalid(problems)
    _write(config_db, key, value, by)
    return value


def delete_prompt(config_db, prompt_id: str, by: str | None) -> None:
    """A custom prompt is deleted; a default one can only be turned off or reset."""
    if prompt_id in default_prompts():
        raise Invalid(
            ["A built-in prompt can't be deleted — turn it off, or reset it."]
        )
    value = _row_value(config_db, f"prompt:{prompt_id}")
    if not value:
        raise Invalid(["There is no such prompt."])
    _write(config_db, f"prompt:{prompt_id}", {**value, "deleted": True}, by)


def save_detector(config_db, name: str, changes: dict, by: str | None) -> dict:
    """``changes``: enabled, weight, lift, members, thresholds{}, templates{}; a
    None (or a value equal to the default) puts it back to its default."""
    spec = insight_rules.DETECTORS.get(name)
    if spec is None:
        raise Invalid([f"There is no detector called {name}."])
    key = f"detector:{name}"
    value = _row_value(config_db, key)
    defaults = {
        "enabled": True,
        "weight": spec.weight,
        "lift": spec.lift,
        "members": list(spec.members) if spec.members is not None else None,
    }
    for k, v in changes.items():
        if k in defaults:
            if v is None or v == defaults[k]:
                value.pop(k, None)
            else:
                value[k] = v
        elif k in ("thresholds", "templates"):
            group = dict(value.get(k) or {})
            for name2, v2 in (v or {}).items():
                dflt = (
                    spec.thresholds[name2].default
                    if k == "thresholds" and name2 in spec.thresholds
                    else spec.templates[name2].default
                    if k == "templates" and name2 in spec.templates
                    else object()
                )
                if v2 is None or v2 == dflt:
                    group.pop(name2, None)
                else:
                    group[name2] = v2
            if group:
                value[k] = group
            else:
                value.pop(k, None)
        else:
            raise Invalid([f"A detector has no setting {k!r}."])
    problems = check_detector(name, value, known_members(config_db))
    if problems:
        raise Invalid(problems)
    _write(config_db, key, value, by)
    return value


def reset(config_db, key: str, by: str | None) -> None:
    """Back to the defaults (undoable). A custom prompt can't be reset — delete it."""
    if key.startswith("prompt:") and key.split(":", 1)[1] not in default_prompts():
        raise Invalid(["A custom prompt has no default — delete it instead."])
    value = _row_value(config_db, key)
    if key == "settings" and "wording_instructions" in value:
        _write(
            config_db,
            key,
            {"wording_version": int(value.get("wording_version") or 0) + 1},
            by,
        )
        return
    keep = (
        {"wording_version": value["wording_version"]}
        if "wording_version" in value
        else {}
    )
    _write(config_db, key, keep, by)


def undo(config_db, key: str, by: str | None) -> None:
    """Swap the row back to its value before the last save."""
    from app.db.config_models import SuggestionConfig

    row = config_db.query(SuggestionConfig).filter(SuggestionConfig.key == key).first()
    if row is None or row.previous is None:
        raise Invalid(["There's nothing to undo."])
    old = copy.deepcopy(row.value)
    back = {} if row.previous == ABSENT else copy.deepcopy(row.previous)
    if key == "settings" and back.get("wording_instructions") != old.get(
        "wording_instructions"
    ):
        back["wording_version"] = (
            max(
                int(old.get("wording_version") or 0),
                int(back.get("wording_version") or 0),
            )
            + 1
        )
    row.value, row.previous, row.updated_by = back, old, by
    config_db.commit()
    clear_cache()
