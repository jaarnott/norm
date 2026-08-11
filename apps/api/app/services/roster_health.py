"""Is the layout-spec roster still coherent?

A supplier spec is a GLOBAL row shared by every venue, edited by an LLM that
sees one invoice at a time. On 11 Aug 2026 that arrangement wrote 'Service
Foods' onto the EUROVINTAGE spec, and from then on every Service Foods invoice
was extracted with a wine wholesaler's prompt — a fault that reinforced itself,
because the next Service Foods sample showed the sensei the wine spec as "the
current spec". Nothing ever looked at the roster as a whole.

These checks are that missing look, and they are deliberately **arithmetic, not
judgement**: the failure was an LLM opinion written to shared state, so a
second LLM opinion is not the cure. Every finding below is a set operation.

**Nothing here reads Loaded.** An earlier draft asked "which spec would each of
Loaded's supplier records select?", which makes spec hygiene a function of how
staff type names in someone else's system — rename a supplier there and a
perfectly good spec reads as broken. Norm's own rows are enough: the specs, and
the dojo samples, which are real invoices carrying Norm's own extraction of who
printed them. (Loaded remains the resolver for supplier NAMES at review time;
that is a different question, asked of the right system.)

Pure functions over plain rows, so CI can test them with no live config DB —
the doctrine ``config_validator`` sets out, and the reason these can also run
inside the write gate with no network call.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass

from app.services.supplier_identity import match_spec, norm, words


@dataclass
class RosterIssue:
    """One incoherence in the spec roster."""

    severity: str  # "error" — actively routing invoices wrong; "warning" — rot
    kind: str  # alias_collision | near_duplicate_spec | misfiled_sample | ...
    where: str  # the spec (and sample) it concerns
    problem: str
    fix: str

    def to_dict(self) -> dict:
        return asdict(self)

    def key(self) -> tuple:
        """Identity for before/after comparison in the gate."""
        return (self.kind, self.where)


def _identity(spec) -> set[str]:
    """Every word the roster claims for this spec."""
    out = words(spec.name)
    for a in spec.aliases or []:
        out |= words(a)
    return out


def _live(specs, main_prompt_name: str) -> list:
    return [
        s for s in specs if s.name != main_prompt_name and getattr(s, "enabled", True)
    ]


def check_alias_collisions(specs, *, main_prompt_name: str) -> list[RosterIssue]:
    """The same name claimed by two specs.

    This IS the incident: 'Service Foods' sat on the Service Foods spec (as its
    name) and the Eurovintage spec (as an alias), and match order decided which
    prompt a food-service invoice got. Writes are guarded now, so this only
    ever finds rows written before the guard — but a silent legacy collision is
    exactly the kind of thing that goes unnoticed for months.
    """
    rows = _live(specs, main_prompt_name)
    issues: list[RosterIssue] = []
    seen: dict[str, tuple] = {}  # normalised claim -> (spec name, "name"|"alias")
    for sp in rows:
        for kind, claim in [("name", sp.name)] + [
            ("alias", a) for a in (sp.aliases or [])
        ]:
            c = norm(claim)
            if len(c) < 3:
                continue
            prior = seen.get(c)
            if prior and prior[0] != sp.name:
                issues.append(
                    RosterIssue(
                        severity="error",
                        kind="alias_collision",
                        where=f"{sp.name} + {prior[0]}",
                        problem=(
                            f"'{claim}' is claimed by both specs "
                            f"(as {kind} here, as {prior[1]} on '{prior[0]}')"
                        ),
                        fix=(
                            "Remove it from whichever spec does not name that "
                            "business — one name, one spec."
                        ),
                    )
                )
            elif not prior:
                seen[c] = (sp.name, kind)
    return issues


def check_near_duplicates(specs, *, main_prompt_name: str) -> list[RosterIssue]:
    """Two specs for one business.

    The rule is deliberately the SUBSET one — every identity word of one spec
    also claimed by another — not "shares a word". Calibrated on the real
    roster: subset flags both genuine cases (the two Trents rows; Eurovintage
    while it held the stolen alias) and nothing else across all 171 pairs,
    while "shares a word" also flags Bidfood/Service Foods ('service') and
    Noisy Brewery/Sawmill ('brewery'), which are different businesses.
    """
    rows = _live(specs, main_prompt_name)
    idents = [(sp, _identity(sp)) for sp in rows]
    issues: list[RosterIssue] = []
    for i, (a, wa) in enumerate(idents):
        for b, wb in idents[i + 1 :]:
            if not wa or not wb:
                continue
            if wa <= wb or wb <= wa:
                broad, narrow = (b, a) if wa <= wb else (a, b)
                issues.append(
                    RosterIssue(
                        severity="warning",
                        kind="near_duplicate_spec",
                        where=f"{narrow.name} + {broad.name}",
                        problem=(
                            f"'{broad.name}' claims every identity word of "
                            f"'{narrow.name}' — these look like one business "
                            "with two rows"
                        ),
                        fix=(
                            "If they are the same business, merge them: one "
                            "spec, with a selector in the text if it prints "
                            "more than one template."
                        ),
                    )
                )
    return issues


def _sample_supplier(sample) -> str | None:
    """Who the DOCUMENT says printed this sample — Norm's own extraction.

    Never Loaded's supplierName: the point of this check is to be independent
    of what any account typed.
    """
    for blob in (
        sample.expected,
        (sample.last_run or {}).get("extraction"),
        (sample.analysis or {}).get("ground_truth"),
    ):
        if isinstance(blob, dict) and blob.get("supplier_name"):
            return str(blob["supplier_name"])
    return None


def check_misfiled_samples(
    specs, samples, *, main_prompt_name: str
) -> list[RosterIssue]:
    """A sample sitting under a spec its own invoice would not select.

    This is how a corrupted roster quietly poisons its own evidence: after the
    stolen alias, two SERVICE FOODS invoices were filed under Eurovintage, so
    that spec's regression baseline became another supplier's paper — while
    the real Service Foods spec had no coverage at all.
    """
    rows = _live(specs, main_prompt_name)
    by_id = {str(s.id): s for s in rows}
    issues: list[RosterIssue] = []
    for sample in samples:
        host = by_id.get(str(sample.spec_id))
        if host is None:
            continue
        printed = _sample_supplier(sample)
        if not printed:
            continue  # never run — nothing to judge it by
        picked, _how = match_spec(rows, [printed], main_prompt_name=main_prompt_name)
        if picked is not None and str(picked.id) != str(host.id):
            issues.append(
                RosterIssue(
                    severity="error",
                    kind="misfiled_sample",
                    where=f"{host.name}/{sample.label}",
                    problem=(
                        f"the copy is printed by '{printed}', which selects the "
                        f"'{picked.name}' spec — so this sample is grading the "
                        "wrong prompt"
                    ),
                    fix=f"Move the sample to '{picked.name}'.",
                )
            )
    return issues


def check_untested_specs(specs, samples, *, main_prompt_name: str) -> list[RosterIssue]:
    """Instructions nothing verifies, and rows nobody ever filled in."""
    rows = _live(specs, main_prompt_name)
    counted: dict[str, int] = {}
    for s in samples:
        counted[str(s.spec_id)] = counted.get(str(s.spec_id), 0) + 1
    issues: list[RosterIssue] = []
    for sp in rows:
        has_text = bool((sp.instructions or "").strip())
        n = counted.get(str(sp.id), 0)
        if has_text and n == 0:
            issues.append(
                RosterIssue(
                    severity="warning",
                    kind="untested_spec",
                    where=sp.name,
                    problem=(
                        "the spec carries extraction instructions but has no "
                        "sample — nothing proves the text reads a real invoice"
                    ),
                    fix="Add one of this supplier's invoices to the dojo.",
                )
            )
        elif not has_text and n == 0:
            issues.append(
                RosterIssue(
                    severity="warning",
                    kind="orphan_spec",
                    where=sp.name,
                    problem=(
                        "no instructions and no samples — an auto-created row "
                        "nobody filled in, occupying the name"
                    ),
                    fix="Delete it, or make it an alias of the spec that covers this layout.",
                )
            )
    return issues


def roster_issues(specs, samples, *, main_prompt_name: str) -> list[RosterIssue]:
    """Every check, over rows the caller supplies. Pure."""
    return [
        *check_alias_collisions(specs, main_prompt_name=main_prompt_name),
        *check_near_duplicates(specs, main_prompt_name=main_prompt_name),
        *check_misfiled_samples(specs, samples, main_prompt_name=main_prompt_name),
        *check_untested_specs(specs, samples, main_prompt_name=main_prompt_name),
    ]


def regressions(
    before: list[RosterIssue],
    after: list[RosterIssue],
    *,
    severity: str | None = "error",
) -> list[RosterIssue]:
    """Findings a change would INTRODUCE.

    Deliberately not "is the roster clean": it never is, and refusing every
    change until it is would block the very repairs that clean it. What must
    never happen is a change making it worse.

    ``severity="error"`` is the GATE's question — only findings that actively
    route invoices wrong (a collision, a misfiled sample) may block a write.
    Warnings must not: moving a misfiled sample off a spec can leave that spec
    with no samples at all, and refusing the repair because it created a
    hygiene warning would trap the roster in its broken state. Pass
    ``severity=None`` to report everything a change introduced.
    """
    had = {i.key() for i in before}
    new = [i for i in after if i.key() not in had]
    return [i for i in new if severity is None or i.severity == severity]
