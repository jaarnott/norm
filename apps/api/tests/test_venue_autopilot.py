"""The ladder a person climbs to auto-receiving, and the rule that it can only
ever be climbed deliberately. (It was a venue's until Oct 2026; the settings
are now each person's — services/approvals.receiving_settings.)

Every rung above `approve_all` authorises Norm to write into Loaded on its own,
and a stock item, unit, brand or supplier created there cannot be taken back
from Norm. So the invariants worth pinning are all refusals: a stored typo must
not read as permission, a caller must not be able to raise the rung, and an
unticked toggle must never be treated as ticked.
"""

import pytest

from app.services import venue_autopilot as VA


class TestTheDefaultIsTheSafestRung:
    def test_a_venue_that_has_never_been_configured_approves_everything(self):
        s = VA.settings_from(None)
        assert s["mode"] == VA.MODE_APPROVE_ALL
        assert all(s[g] is False for g in VA.GATES)

    def test_every_gate_defaults_off(self):
        """Named separately because this is the whole safety story: turning on
        autopilot must not silently authorise four kinds of Loaded write."""
        s = VA.settings_from({"mode": VA.MODE_AUTOPILOT})
        assert s["mode"] == VA.MODE_AUTOPILOT
        assert all(s[g] is False for g in VA.GATES)

    def test_a_bad_mode_falls_back_rather_than_carrying_on(self):
        s = VA.settings_from({"mode": "AUTOPILOT!!"})
        assert s["mode"] == VA.MODE_APPROVE_ALL

    def test_junk_in_the_column_is_not_permission(self):
        for junk in ("autopilot", ["autopilot"], 7):
            assert VA.settings_from(junk)["mode"] == VA.MODE_APPROVE_ALL

    def test_an_unknown_key_is_dropped(self):
        s = VA.settings_from({"mode": "autopilot", "auto_delete_invoices": True})
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
        s = VA.settings_from({"mode": "autopilot"})
        assert VA.gate_open(s, VA.AUTO_CREATE_ITEMS) is False

    def test_a_ticked_gate_is_open(self):
        s = VA.settings_from({"mode": "autopilot", "auto_create_items": True})
        assert VA.gate_open(s, VA.AUTO_CREATE_ITEMS) is True

    def test_an_unknown_gate_is_never_open(self):
        """A blocker naming a gate nobody defined must stop the invoice, not
        wave it through — a typo in a blocker is not authorisation."""
        s = VA.settings_from({"mode": "autopilot", "auto_create_everything": True})
        assert VA.gate_open(s, "auto_create_everything") is False
        assert VA.gate_open(s, None) is False

    def test_every_gate_can_describe_itself(self):
        """The card names the toggle that would have let an invoice through, so
        a gate with no wording would render a blank reason."""
        for gate in VA.GATES:
            assert VA.describe_gate(gate)
