"""An insight carried into the conversation it started.

When someone clicks an insight suggestion on Home, the new thread remembers
it (``threads.insight_id``) and every turn of that conversation is told its
facts — so when the person explains it ("First Table is our promo until the
end of November") or decides what to do ("we'll cut the Tuesday roster"),
Norm records that with ``norm.note_insight`` and the daily insights take it
into account (docs/plans/home-suggested-prompts.md).
"""

from __future__ import annotations

from sqlalchemy.orm import Session


def insight_dict(db: Session, insight_id: str | None) -> dict | None:
    from app.db.models import Insight, InsightNote, Venue

    if not insight_id:
        return None
    row = db.query(Insight).filter(Insight.id == insight_id).first()
    if row is None:
        return None
    venue = db.query(Venue).filter(Venue.id == row.venue_id).first()
    notes = (
        db.query(InsightNote)
        .filter(InsightNote.subject_key == row.subject_key)
        .order_by(InsightNote.created_at.desc())
        .limit(3)
        .all()
    )
    return {
        "id": row.id,
        "venue_id": row.venue_id,
        "venue": venue.name if venue else None,
        "detector": row.detector,
        "subject": row.subject,
        "area": row.area,
        "headline": row.headline,
        "facts": row.prompt,
        "value": float(row.value or 0),
        "as_of": row.as_of,
        "notes": [
            {"verdict": n.verdict, "reason": n.reason, "until": n.until} for n in notes
        ],
    }


def from_suggestion(
    db: Session, user_id: str | None, suggestion_id: str | None
) -> dict | None:
    """Record the click; return the insight when the suggestion was one the
    person may see (their org, a venue they can access)."""
    from app.db.models import Insight, User
    from app.services.suggestions.events import record_event
    from app.services.entitlements import org_id_for_user
    from app.services.venue_service import user_can_access_venue

    if not suggestion_id or not user_id:
        return None
    user = db.query(User).filter(User.id == user_id).first()
    if user is None:
        return None
    record_event(db, user, suggestion_id, "clicked")
    if not suggestion_id.startswith("insight:"):
        db.commit()
        return None
    row = db.query(Insight).filter(Insight.id == suggestion_id.split(":", 1)[1]).first()
    if (
        row is None
        or row.organization_id != org_id_for_user(user_id, db)
        or not user_can_access_venue(db, user_id, row.venue_id)
    ):
        db.commit()
        return None
    db.commit()
    return insight_dict(db, row.id)


#: What Norm is told in a chat that began from an insight. A default:
#: Settings → AI team → Suggestions can change it (suggestions/config.py).
#: Placeholders: {insight_id} {venue} {as_of} {headline} {notes}.
GUIDANCE = """## This conversation began from an insight
The person clicked an insight Norm raised on their Home screen (insight_id `{insight_id}`,
{venue}, numbers to {as_of}):
**{headline}**
{notes}
Answer their question with the data as usual. Then listen for two things, at any point in
this conversation:
- **They explain it** — it is planned or known (a promotion, a minimum crew, a one-off
  event, a supplier deal). Record it with `note_insight` (verdict `expected`, their reason,
  and `until` if they give an end date), and tell them in one line that Norm will treat it
  as expected.
- **They decide to act** ("we'll cut the Tuesday roster", "I'll switch suppliers"). Record
  it with `note_insight` (verdict `acting`, what they will do), and say Norm will check the
  result in a few weeks.
If they say the insight isn't useful to them, record verdict `not_useful`. Never record a
note they didn't give you, and never more than one per thing they said.
"""


def guidance(insight: dict | None, config_db=None) -> str:
    """The prompt section for a conversation that began from an insight."""
    from app.services.insights.rules import render_text
    from app.services.suggestions import config as suggestion_config

    if not insight:
        return ""
    noted = ""
    if insight.get("notes"):
        lines = "\n".join(
            f"- {n['verdict']}: {n.get('reason') or ''}{' (until ' + n['until'] + ')' if n.get('until') else ''}"
            for n in insight["notes"]
        )
        noted = f"\nWhat people have already said about it:\n{lines}\n"
    facts = {
        "insight_id": insight["id"],
        "venue": insight.get("venue") or "their venue",
        "as_of": insight.get("as_of"),
        "headline": insight["headline"],
        "notes": noted,
    }
    template = suggestion_config.current(config_db).settings.get("insight_guidance")
    try:
        body = render_text(template or GUIDANCE, facts)
    except (KeyError, ValueError, IndexError):
        body = render_text(GUIDANCE, facts)
    return "\n\n" + body
