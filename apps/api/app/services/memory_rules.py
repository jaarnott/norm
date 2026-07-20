"""Admission control for learned memory.

Auto-writing memories is only safe because most things do not qualify. These
rules are the mechanism — not the review queue, which is a backstop. They run
server-side in ``norm__remember`` so the model cannot talk its way past them.

The ordering matters and is not arbitrary:

    Rule 2 (reject)  →  Rule 1 (must match a type)  →  Rule 3 (scope)

Rejection is checked *first* because it is the safety rule. A candidate that
looks like a tidy preference ("always use the 7am trading day") is exactly the
kind of thing that passes a type check and must still be refused.

Why Rule 2 exists at all, concretely: Norm reports money. A remembered
"business rule" is advice the model may or may not follow, and advice fails
silently — the trading-day incident produced a confident $0 for a Saturday
because a rule lived as guidance instead of as enforced code. So anything that
would change a number, or gate money, is refused here and belongs in
``business_calendar`` or the approval gate instead. Memory carries judgement
only.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

#: Rule 1 — the closed list. A candidate must be exactly one of these.
MEMORY_TYPES = ("vocabulary", "preference", "context", "correction")

VALID_SCOPES = ("user", "org")


@dataclass(frozen=True)
class Verdict:
    """Outcome of admission control."""

    accepted: bool
    #: Set when accepted.
    scope: str | None = None
    #: Set when rejected — shown to the model so it stops re-proposing.
    reason: str | None = None
    #: Where the fact should live instead, when we can say.
    belongs_in: str | None = None

    @property
    def rejected(self) -> bool:
        return not self.accepted


# ── Rule 2: what must never be stored ────────────────────────────────────
#
# Matched on the candidate's text. These are deliberately broad: a false
# rejection costs one un-learned preference, while a false acceptance puts an
# unenforced business rule into the answer path. The asymmetry justifies being
# blunt.

_NUMBER_RULES = re.compile(
    r"\b("
    r"trading day|business day|day start|starts at \d|day_start|"
    r"midnight to midnight|financial year|tax rate|gst|markup|margin formula|"
    r"round(?:ing|ed)? (?:up|down|to)|calculate\w* as|is defined as"
    r")\b",
    re.IGNORECASE,
)

_MONEY_GATES = re.compile(
    r"\b("
    r"approv\w+|auto[- ]?receive|authoris\w+|authoriz\w+|sign[- ]?off|"
    r"spend limit|credit limit|budget cap|without asking|no approval|"
    r"over \$?\d|under \$?\d|up to \$?\d"
    r")\b",
    re.IGNORECASE,
)

# Things a connector or the database already answers. Remembering them means
# serving a stale copy of something we could just look up.
_QUERYABLE = re.compile(
    r"^\s*(?:the\s+)?(?:venues?|staff|employees?|suppliers?|stock items?|"
    r"products?|recipes?|opening hours)\s+(?:are|is|include|list)\b",
    # MULTILINE because admit() matches against "title\nbody", so the phrase
    # being tested usually starts on the second line.
    re.IGNORECASE | re.MULTILINE,
)

# A measurement, not a standing fact. Goes stale the moment it is written and
# is cheap to re-read.
_OBSERVATION = re.compile(
    r"(\$\s?[\d,]+(?:\.\d+)?|\b\d[\d,]*\.?\d*\s*(?:sales|revenue|covers|orders)\b)",
    re.IGNORECASE,
)

_PII = re.compile(
    r"\b("
    r"pay rate|hourly rate|salary|wage|performance review|disciplinary|"
    r"sick (?:leave|note)|medical|visa status|bank account|ird number"
    r")\b",
    re.IGNORECASE,
)

_NEVER = (
    (
        _NUMBER_RULES,
        "This defines how a figure is calculated, so it must be enforced in "
        "code rather than remembered — advice can be silently ignored and "
        "produce a confidently wrong number.",
        "business_calendar / the tool interface",
    ),
    (
        _MONEY_GATES,
        "This gates money or an action, so it belongs to the approval policy, "
        "not to memory.",
        "workflow modes / the approval gate",
    ),
    (
        _QUERYABLE,
        "This can be queried live, and a query is always fresher than a memory.",
        "the connector",
    ),
    (
        _OBSERVATION,
        "This is an observation of data rather than a durable fact about the "
        "business; it is stale as soon as it is written.",
        "re-read it from the source",
    ),
    (
        _PII,
        "This is personal employee data and is never needed to shape an answer.",
        "the HR system of record",
    ),
)


def check_forbidden(text: str) -> tuple[str, str] | None:
    """Rule 2. Returns (reason, belongs_in) if the text must not be stored."""
    for pattern, reason, belongs_in in _NEVER:
        if pattern.search(text or ""):
            return reason, belongs_in
    return None


# ── Rule 3: scope ────────────────────────────────────────────────────────
#
# "Would a colleague asking the same question want a different answer?"
# Preferences are about a person; vocabulary and operational context are facts
# about the business.

_SCOPE_BY_TYPE = {
    "preference": "user",
    "correction": "user",
    "vocabulary": "org",
    "context": "org",
}

_FIRST_PERSON = re.compile(r"\b(I|me|my|mine)\b")


def infer_scope(memory_type: str, text: str) -> str:
    """Rule 3. Which scope a candidate belongs to."""
    scope = _SCOPE_BY_TYPE.get(memory_type, "user")
    # A correction phrased as a fact about the business is an org fact even
    # though corrections usually reflect one person's preference.
    if memory_type == "correction" and not _FIRST_PERSON.search(text or ""):
        return "org"
    return scope


def admit(
    memory_type: str,
    title: str,
    body: str,
    requested_scope: str | None = None,
) -> Verdict:
    """Run admission control over a candidate memory.

    Order is Rule 2 → Rule 1 → Rule 3; see the module docstring for why
    rejection comes first.
    """
    text = f"{title}\n{body}".strip()

    if not title or not body:
        return Verdict(False, reason="A memory needs both a title and a body.")

    forbidden = check_forbidden(text)
    if forbidden:
        reason, belongs_in = forbidden
        return Verdict(False, reason=reason, belongs_in=belongs_in)

    if memory_type not in MEMORY_TYPES:
        return Verdict(
            False,
            reason=(
                f"'{memory_type}' is not a memory type. Must be one of: "
                f"{', '.join(MEMORY_TYPES)}. If it fits none of them, it is "
                "not something to remember."
            ),
        )

    scope = infer_scope(memory_type, text)
    # A caller may narrow to user scope but never widen to org — widening is
    # what makes one person's opinion everybody's answer.
    if requested_scope == "user":
        scope = "user"
    elif requested_scope not in (None, "org", "user"):
        return Verdict(False, reason=f"Unknown scope '{requested_scope}'.")

    return Verdict(True, scope=scope)


def needs_confirmation(scope: str, trigger: str | None) -> bool:
    """Rule 4 routing: may this be written without a human confirming?

    Org-scoped writes always queue. Claude's own memory auto-writes because it
    serves one person; an org memory changes *other people's* answers, so the
    same latitude is not available here.

    User-scoped writes auto-save only on the two high-signal triggers. An
    inferred preference is a guess and waits for review.
    """
    if scope != "user":
        return True
    return trigger not in ("explicit", "correction")
