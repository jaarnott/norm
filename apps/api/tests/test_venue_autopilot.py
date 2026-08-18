"""The ladder a venue climbs to auto-receiving, and the rule that it can only
ever be climbed deliberately.

Every rung above `approve_all` authorises Norm to write into Loaded on its own,
and a stock item, unit, brand or supplier created there cannot be taken back
from Norm. So the invariants worth pinning are all refusals: a stored typo must
not read as permission, a caller must not be able to raise the rung, and an
unticked toggle must never be treated as ticked.
"""

import pytest

from app.services import venue_autopilot as VA


class _Venue:
    def __init__(self, stored=None):
        self.invoice_autopilot = stored


class TestTheDefaultIsTheSafestRung:
    def test_a_venue_that_has_never_been_configured_approves_everything(self):
        s = VA.settings_for(_Venue())
        assert s["mode"] == VA.MODE_APPROVE_ALL
        assert all(s[g] is False for g in VA.GATES)

    def test_every_gate_defaults_off(self):
        """Named separately because this is the whole safety story: turning on
        autopilot must not silently authorise four kinds of Loaded write."""
        s = VA.settings_for(_Venue({"mode": VA.MODE_AUTOPILOT}))
        assert s["mode"] == VA.MODE_AUTOPILOT
        assert all(s[g] is False for g in VA.GATES)

    def test_a_bad_mode_falls_back_rather_than_carrying_on(self):
        s = VA.settings_for(_Venue({"mode": "AUTOPILOT!!"}))
        assert s["mode"] == VA.MODE_APPROVE_ALL

    def test_junk_in_the_column_is_not_permission(self):
        for junk in ("autopilot", ["autopilot"], 7):
            assert VA.settings_for(_Venue(junk))["mode"] == VA.MODE_APPROVE_ALL

    def test_an_unknown_key_is_dropped(self):
        s = VA.settings_for(_Venue({"mode": "autopilot", "auto_delete_invoices": True}))
        assert "auto_delete_invoices" not in s


class TestTheCallerCanOnlyLowerTheRung:
    @pytest.mark.parametrize(
        "venue,asked,expected",
        [
            # Reviewing ONE invoice passes approve_all — opening an invoice in
            # the card must never write to Loaded, whatever the venue allows.
            ("autopilot", "approve_all", "approve_all"),
            ("autopilot", "approve_fixes", "approve_fixes"),
            ("approve_fixes", "autopilot", "approve_fixes"),
            ("approve_all", "autopilot", "approve_all"),
            ("autopilot", None, "autopilot"),
            # "No limit asked for" is not "limit to nothing". The mode injected
            # for a user with no personal preference is the literal "unset", so
            # reading it as approve_all pinned every venue there whatever it
            # was set to — the feature switched off by its own safety rail.
            ("autopilot", "unset", "autopilot"),
            ("autopilot", "turbo", "autopilot"),
            ("approve_fixes", "unset", "approve_fixes"),
        ],
    )
    def test_the_lower_rung_wins(self, venue, asked, expected):
        assert VA.at_most(venue, asked) == expected

    def test_a_venue_on_approve_all_cannot_be_talked_into_autopilot(self):
        """The one that matters: a chat request or a stale scheduled task must
        not be able to receive at a venue that never opted in."""
        assert VA.at_most(VA.MODE_APPROVE_ALL, VA.MODE_AUTOPILOT) == VA.MODE_APPROVE_ALL


class TestGates:
    def test_an_unticked_gate_is_shut(self):
        s = VA.settings_for(_Venue({"mode": "autopilot"}))
        assert VA.gate_open(s, VA.AUTO_CREATE_ITEMS) is False

    def test_a_ticked_gate_is_open(self):
        s = VA.settings_for(_Venue({"mode": "autopilot", "auto_create_items": True}))
        assert VA.gate_open(s, VA.AUTO_CREATE_ITEMS) is True

    def test_an_unknown_gate_is_never_open(self):
        """A blocker naming a gate nobody defined must stop the invoice, not
        wave it through — a typo in a blocker is not authorisation."""
        s = VA.settings_for(
            _Venue({"mode": "autopilot", "auto_create_everything": True})
        )
        assert VA.gate_open(s, "auto_create_everything") is False
        assert VA.gate_open(s, None) is False

    def test_every_gate_can_describe_itself(self):
        """The card names the toggle that would have let an invoice through, so
        a gate with no wording would render a blank reason."""
        for gate in VA.GATES:
            assert VA.describe_gate(gate)


class TestNormalise:
    def test_it_refuses_a_mode_that_does_not_exist(self):
        with pytest.raises(ValueError, match="mode must be one of"):
            VA.normalise({"mode": "yolo"})

    def test_it_fills_in_every_gate(self):
        out = VA.normalise({"mode": "autopilot", "auto_create_brands": True})
        assert out["auto_create_brands"] is True
        assert out["auto_create_items"] is False
        assert set(out) == {"mode", *VA.GATES}


class TestTheEndpointsExist:
    """The panel read "Loading…" forever against a server that 404'd on these
    routes, and nothing caught it because they had no test. A route that isn't
    exercised isn't shipped.
    """

    def test_a_fresh_venue_reports_the_safest_rung(
        self, client, admin_headers, db_session
    ):
        from tests.conftest import _make_organization, _make_venue

        org = _make_organization(db_session, name="Ladder Co")
        venue = _make_venue(db_session, name="Ladder", organization_id=org.id)

        res = client.get(
            f"/api/venues/{venue.id}/invoice-autopilot", headers=admin_headers
        )

        assert res.status_code == 200, res.text
        body = res.json()
        assert body["settings"]["mode"] == VA.MODE_APPROVE_ALL
        assert all(body["settings"][g] is False for g in VA.GATES)
        # The card and the settings page both render these words, so they come
        # from the server rather than each keeping a copy that drifts.
        assert set(body["gates"]) == set(VA.GATES)

    def test_it_saves_a_rung_and_a_toggle(self, client, admin_headers, db_session):
        from tests.conftest import _make_organization, _make_venue

        org = _make_organization(db_session, name="Ladder Co 2")
        venue = _make_venue(db_session, name="Ladder 2", organization_id=org.id)

        res = client.put(
            f"/api/venues/{venue.id}/invoice-autopilot",
            headers=admin_headers,
            json={"mode": "autopilot", "auto_create_brands": True},
        )

        assert res.status_code == 200, res.text
        assert res.json()["settings"]["auto_create_brands"] is True
        # And it survives a round trip rather than living in the response only.
        again = client.get(
            f"/api/venues/{venue.id}/invoice-autopilot", headers=admin_headers
        )
        assert again.json()["settings"]["mode"] == "autopilot"
        assert again.json()["settings"]["auto_create_items"] is False

    def test_a_mode_that_does_not_exist_is_refused(
        self, client, admin_headers, db_session
    ):
        from tests.conftest import _make_organization, _make_venue

        org = _make_organization(db_session, name="Ladder Co 3")
        venue = _make_venue(db_session, name="Ladder 3", organization_id=org.id)

        res = client.put(
            f"/api/venues/{venue.id}/invoice-autopilot",
            headers=admin_headers,
            json={"mode": "turbo"},
        )
        assert res.status_code == 400

    def test_an_unknown_venue_is_a_404_not_a_new_row(self, client, admin_headers):
        res = client.get(
            "/api/venues/does-not-exist/invoice-autopilot", headers=admin_headers
        )
        assert res.status_code == 404
