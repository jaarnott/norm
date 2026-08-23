"""build_roster_data — the source-of-truth combiner for the roster editor.

Pins the payload contract the prepare_roster consolidator must mirror and the
roster_editor component reads: roster meta + rosteredShifts at top level, the
four reference lists nested under _-prefixed keys.
"""

from app.services.roster_document import build_roster_data


class TestBuildRosterData:
    def test_combines_roster_and_reference_lists(self):
        roster = {
            "id": "r1",
            "startDateTime": "2026-07-27T07:00:00+12:00",
            "endDateTime": "2026-08-03T06:59:59+12:00",
            "totalHours": 40,
            "rosteredShifts": [{"id": "s1", "staffMemberId": "m1"}],
        }
        data = build_roster_data(
            roster,
            staff=[{"id": "m1", "name": "Alex"}],
            roles=[{"id": "role1", "name": "Bar"}],
            leave=[{"id": "l1"}],
            unavailability=[{"id": "u1"}],
        )
        # roster body preserved at top level (what extractShifts/extractRosterMeta read)
        assert data["id"] == "r1"
        assert data["rosteredShifts"] == [{"id": "s1", "staffMemberId": "m1"}]
        assert data["totalHours"] == 40
        # reference lists nested under _-prefixed keys
        assert data["_staff"] == [{"id": "m1", "name": "Alex"}]
        assert data["_roles"] == [{"id": "role1", "name": "Bar"}]
        assert data["_leave"] == [{"id": "l1"}]
        assert data["_unavailability"] == [{"id": "u1"}]

    def test_accepts_a_list_roster(self):
        data = build_roster_data([{"id": "r1", "rosteredShifts": []}], staff=[])
        assert data["id"] == "r1"
        assert data["_staff"] == []

    def test_empty_roster_is_a_clean_dict_not_invented_fields(self):
        data = build_roster_data(None)
        assert data == {
            "_staff": [],
            "_roles": [],
            "_leave": [],
            "_unavailability": [],
        }

    def test_non_list_reference_data_defaults_to_empty(self):
        data = build_roster_data({"id": "r1"}, staff=None, roles="oops")
        assert data["_staff"] == []
        assert data["_roles"] == []

    def test_does_not_mutate_the_input_roster(self):
        roster = {"id": "r1", "rosteredShifts": []}
        build_roster_data(roster, staff=[{"id": "m1"}])
        assert "_staff" not in roster  # combiner copied, didn't mutate
