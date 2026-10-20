"""The autopilot-confidence report.

The report exists to answer one question honestly — "would autopilot have been
right?" — so the tests here mostly pin what must be EXCLUDED from the rates:
Norm's own self-fulfilling receives, and invoices nobody ever reviewed.
"""

from datetime import datetime, timezone


from app.db.models import InvoiceAutopilotOutcome

from .conftest import _make_organization, _make_venue


def _venue(db, name="Bessie"):
    org = _make_organization(db, name=f"{name} Co")
    return _make_venue(db, name=name, organization_id=org.id)


def _row(db, venue, outcome, **over):
    row = InvoiceAutopilotOutcome(
        venue_id=venue.id,
        organization_id=venue.organization_id,
        invoice_id=over.pop("invoice_id", f"inv-{outcome}-{id(over)}"),
        supplier_name=over.pop("supplier_name", "Bidfood"),
        outcome=outcome,
        received=outcome != "dojo",
        mode=over.pop("mode", "interactive"),
        actor=over.pop("actor", "user"),
        created_at=datetime.now(timezone.utc),
        **over,
    )
    db.add(row)
    db.flush()
    return row


class TestReport:
    def _get(self, client, headers, **params):
        # Always venue-scoped: the endpoint reads the whole table, so an
        # unscoped assertion would depend on what other tests happened to
        # record (receives elsewhere in the suite now write rows too).
        q = "&".join(f"{k}={v}" for k, v in params.items())
        return client.get(
            f"/api/supplier-invoice-specs/autopilot-confidence{'?' + q if q else ''}",
            headers=headers,
        )

    def test_requires_admin(self, client, manager_headers):
        assert self._get(client, manager_headers).status_code == 403

    def test_norm_rows_never_touch_the_headline_rate(
        self, client, admin_headers, db_session
    ):
        # Autopilot accepted everything a line before receiving, so its own
        # rows are clean by construction — counting them would make the
        # readiness number meaningless.
        v = _venue(db_session)
        _row(db_session, v, "clean", suggestion_count=2, accepted_count=2)
        _row(db_session, v, "edited", invoice_id="i-2", suggestion_count=2)
        for i in range(8):
            _row(
                db_session,
                v,
                "clean",
                invoice_id=f"norm-{i}",
                actor="norm",
                mode="autopilot",
                suggestion_count=3,
            )
        body = self._get(client, admin_headers, venue_id=v.id).json()
        assert body["totals"]["attempts"] == 2  # humans only
        assert body["rates"]["autopilot_ready"] == 0.5
        assert body["autopilot"]["attempts"] == 8  # reported, separately

    def test_zero_suggestion_invoices_leave_the_quality_rate_alone(
        self, client, admin_headers, db_session
    ):
        # They belong in "would autopilot have been right?" but NOT in "when
        # Norm spoke, was it right?" — otherwise the quality rate inflates.
        v = _venue(db_session)
        _row(db_session, v, "clean", invoice_id="a", suggestion_count=2)
        _row(db_session, v, "edited", invoice_id="b", suggestion_count=2)
        for i in range(6):
            _row(db_session, v, "no_suggestions", invoice_id=f"n-{i}")
        body = self._get(client, admin_headers, venue_id=v.id).json()
        assert body["rates"]["suggestion_quality"] == 0.5  # 1 clean of 2 spoken
        assert body["rates"]["autopilot_ready"] == 0.875  # 7 of 8

    def test_not_reviewed_is_in_no_denominator(self, client, admin_headers, db_session):
        v = _venue(db_session)
        _row(db_session, v, "clean", invoice_id="a", suggestion_count=1)
        _row(db_session, v, "not_reviewed", invoice_id="b")
        body = self._get(client, admin_headers, venue_id=v.id).json()
        assert body["totals"]["not_reviewed"] == 1
        assert body["rates"]["autopilot_ready"] == 1.0

    def test_a_deleted_draft_is_in_no_rate(self, client, admin_headers, db_session):
        """Norm's deletes are recorded with their reason, but a statement or a
        duplicate was never a receive — it would read as an unclean attempt."""
        v = _venue(db_session)
        _row(db_session, v, "clean", invoice_id="a", suggestion_count=1)
        for actor in ("user", "norm"):
            _row(
                db_session,
                v,
                "deleted",
                invoice_id=f"gone-{actor}",
                actor=actor,
                suggestion_count=1,
            )
        body = self._get(client, admin_headers, venue_id=v.id, actor="all").json()
        assert body["totals"]["attempts"] == 1
        assert body["rates"]["suggestion_quality"] == 1.0

    def test_per_supplier_and_missed_fields(self, client, admin_headers, db_session):
        v = _venue(db_session)
        _row(db_session, v, "clean", invoice_id="a", suggestion_count=1)
        _row(
            db_session,
            v,
            "edited",
            invoice_id="b",
            supplier_name="Hancocks",
            suggestion_count=1,
            manual_edit_count=1,
            detail={"manual_fields": ["line:ld-9.unit_cost"]},
        )
        body = self._get(client, admin_headers, venue_id=v.id).json()
        names = {s["supplier_name"]: s for s in body["suppliers"]}
        assert names["Bidfood"]["autopilot_ready"] == 1.0
        assert names["Hancocks"]["autopilot_ready"] == 0.0
        # Normalised so the same field on different lines aggregates.
        assert body["top_missed_fields"][0] == {"field": "line.unit_cost", "count": 1}


class TestFlagsAndAutoVerdicts:
    """The per-flag sentence: 'with X on, N of your receives would have
    auto-received identically'. Aggregated from detail.auto (the end-state
    verdict recorded at receive)."""

    def _get(self, client, headers, **params):
        q = "&".join(f"{k}={v}" for k, v in params.items())
        return client.get(
            f"/api/supplier-invoice-specs/autopilot-confidence{'?' + q if q else ''}",
            headers=headers,
        )

    def test_flags_count_sole_and_combined_unlocks(
        self, client, admin_headers, db_session, venue
    ):
        _row(
            db_session,
            venue,
            "clean",
            detail={
                "auto": {
                    "verdict": "matched",
                    "gates_needed": ["receive_without_po"],
                    "ungated": [],
                    "diffs": [],
                }
            },
        )
        _row(
            db_session,
            venue,
            "clean",
            detail={
                "auto": {
                    "verdict": "matched",
                    "gates_needed": ["receive_without_po"],
                    "ungated": [],
                    "diffs": [],
                }
            },
        )
        _row(
            db_session,
            venue,
            "clean",
            detail={
                "auto": {
                    "verdict": "matched",
                    "gates_needed": ["receive_without_po", "auto_create_units"],
                    "ungated": [],
                    "diffs": [],
                }
            },
        )
        _row(
            db_session,
            venue,
            "edited",
            detail={
                "auto": {
                    "verdict": "differed",
                    "gates_needed": ["auto_create_units"],
                    "ungated": [],
                    "diffs": [
                        {"path": "line:x.unit", "sent": "Each", "auto": "6x700ml"}
                    ],
                }
            },
        )
        _row(
            db_session,
            venue,
            "clean",
            detail={
                "auto": {
                    "verdict": "never_auto",
                    "gates_needed": [],
                    "ungated": ["totals_inconsistent"],
                    "diffs": [],
                }
            },
        )
        res = self._get(client, admin_headers, venue_id=venue.id)
        assert res.status_code == 200, res.text
        body = res.json()
        assert body["auto"] == {
            "matched": 3,
            "differed": 1,
            "never_auto": 1,
            "unscored": 0,
            "no_flags_needed": 0,
        }
        po = next(f for f in body["flags"] if f["gate"] == "receive_without_po")
        assert po["sole_unlock"] == 2 and po["with_others"] == 1
        units = next(f for f in body["flags"] if f["gate"] == "auto_create_units")
        # the differed row never counts toward a flag's unlocks
        assert units["sole_unlock"] == 0 and units["with_others"] == 1
        # the viewer's rows carry the verdict + diffs
        recent_auto = [r.get("auto") for r in body["recent"] if r.get("auto")]
        assert len(recent_auto) == 5
        assert any(
            (a.get("diffs") or [{}])[0].get("auto") == "6x700ml" for a in recent_auto
        )
