"""Response transform — the `recompute` step.

Filtering rows out of a nested array leaves any top-level total describing rows
that are no longer in the payload. That shipped: `get_roster` fetched every
venue's shifts, kept LoadedHub's all-venue `totalHours`, then filtered the list
down to one venue. A week holding 66 shifts worth 146.5 hours reported 332.25 —
and nothing errored, because both numbers were individually well-formed.

It surfaced only when one agent consulted another and the two answers differed
by 2.3x.
"""

from app.connectors.response_transform import apply_response_transform


def _roster(shifts):
    return {
        "id": "r1",
        "totalHours": 999.0,  # deliberately wrong: the stale all-venue figure
        "rosteredShifts": shifts,
    }


SHIFTS = [
    {"totalHours": 10.0, "isFromOtherVenue": False},
    {"totalHours": 5.5, "isFromOtherVenue": False},
    {"totalHours": 99.0, "isFromOtherVenue": True},
]

CONFIG = {
    "enabled": True,
    "fields": {
        "id": "id",
        "totalHours": "totalHours",
        "rosteredShifts[].totalHours": "totalHours",
        "rosteredShifts[].isFromOtherVenue": "",
    },
    "filters": [
        {
            "field": "rosteredShifts[].isFromOtherVenue",
            "operator": "equals",
            "value": "false",
        }
    ],
    "recompute": [
        {"field": "totalHours", "from": "rosteredShifts[].totalHours", "op": "sum"}
    ],
}


class TestRecompute:
    def test_the_roster_bug(self):
        """The regression: total must describe the rows that survived."""
        out = apply_response_transform([_roster(SHIFTS)], CONFIG)[0]
        assert len(out["rosteredShifts"]) == 2
        assert out["totalHours"] == 15.5, "stale all-venue total leaked through"

    def test_without_recompute_the_total_goes_stale(self):
        """Pin the old behaviour, so the reason this exists stays visible."""
        cfg = {k: v for k, v in CONFIG.items() if k != "recompute"}
        out = apply_response_transform([_roster(SHIFTS)], cfg)[0]
        assert len(out["rosteredShifts"]) == 2
        assert out["totalHours"] == 999.0  # describes rows that are gone

    def test_single_object_payload(self):
        out = apply_response_transform(_roster(SHIFTS), CONFIG)
        assert out["totalHours"] == 15.5

    def test_each_roster_in_a_list_is_totalled_separately(self):
        out = apply_response_transform([_roster(SHIFTS), _roster(SHIFTS[:1])], CONFIG)
        assert [r["totalHours"] for r in out] == [15.5, 10.0]

    def test_no_surviving_rows_gives_zero_not_the_stale_value(self):
        out = apply_response_transform([_roster([SHIFTS[2]])], CONFIG)[0]
        assert out["rosteredShifts"] == []
        assert out["totalHours"] == 0

    def test_count_operator(self):
        cfg = {
            **CONFIG,
            "recompute": [
                {
                    "field": "totalHours",
                    "from": "rosteredShifts[].totalHours",
                    "op": "count",
                }
            ],
        }
        assert apply_response_transform([_roster(SHIFTS)], cfg)[0]["totalHours"] == 2

    def test_floats_are_rounded(self):
        """0.1 + 0.2 must not reach an agent as 0.30000000000000004."""
        shifts = [
            {"totalHours": 0.1, "isFromOtherVenue": False},
            {"totalHours": 0.2, "isFromOtherVenue": False},
        ]
        assert (
            apply_response_transform([_roster(shifts)], CONFIG)[0]["totalHours"] == 0.3
        )

    def test_non_numeric_and_missing_values_are_skipped(self):
        shifts = [
            {"totalHours": 4.0, "isFromOtherVenue": False},
            {"totalHours": None, "isFromOtherVenue": False},
            {"isFromOtherVenue": False},
            {"totalHours": "n/a", "isFromOtherVenue": False},
        ]
        assert (
            apply_response_transform([_roster(shifts)], CONFIG)[0]["totalHours"] == 4.0
        )

    def test_booleans_are_not_counted_as_numbers(self):
        shifts = [
            {"totalHours": 4.0, "isFromOtherVenue": False},
            {"totalHours": True, "isFromOtherVenue": False},
        ]
        assert (
            apply_response_transform([_roster(shifts)], CONFIG)[0]["totalHours"] == 4.0
        )

    def test_unknown_op_is_left_alone_rather_than_guessed(self):
        cfg = {
            **CONFIG,
            "recompute": [
                {
                    "field": "totalHours",
                    "from": "rosteredShifts[].totalHours",
                    "op": "median",
                }
            ],
        }
        assert (
            apply_response_transform([_roster(SHIFTS)], cfg)[0]["totalHours"] == 999.0
        )

    def test_malformed_rules_do_not_raise(self):
        for bad in ([{}], [{"field": "totalHours"}], ["nonsense"], [{"from": "a[].b"}]):
            cfg = {**CONFIG, "recompute": bad}
            out = apply_response_transform([_roster(SHIFTS)], cfg)[0]
            assert out["totalHours"] == 999.0

    def test_absent_recompute_key_changes_nothing(self):
        """The other 143 tools declare no recompute and must be unaffected."""
        cfg = {k: v for k, v in CONFIG.items() if k != "recompute"}
        assert (
            apply_response_transform([_roster(SHIFTS)], cfg)[0]["totalHours"] == 999.0
        )
