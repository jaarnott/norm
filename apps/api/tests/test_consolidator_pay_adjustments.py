"""``loadedhub.get_pay_adjustments`` — Loaded's pay adjustments and the public
holidays among them, exec'd under the real sandbox namespace
(tests/consolidator_harness.py) over a raw packages payload shaped like the
live one (Bessie & Royals, 23 Oct 2026: the managed New Zealand Public
Holidays package and a Canterbury Anniversary Day package activated for one
day only), run through the REAL shape from shapes.json first — production
applies it before the code sees the payload, converting the activation
instants to the venue's zone.
"""

from __future__ import annotations

import copy
import datetime as dt
import json

from app.connectors.response_transform import apply_response_transform
from tests import consolidator_harness as harness

CODE = harness.source("get_pay_adjustments")
SHAPE = json.loads((harness.CONSOLIDATORS / "shapes.json").read_text())[
    "loadedhub.get_pay_adjustments"
]["loadedhub.get_pay_adjustment_packages_raw"]


def _dated(rule_id: str, *dates: str, deleted=None, repeat="Never") -> list[dict]:
    return [
        {
            "id": f"{rule_id}-{d}",
            "deletedAt": deleted,
            "ruleId": rule_id,
            "code": "ScheduleMatch",
            "payload": {
                "datestampStart": f"{d}T00:00:00",
                "datestampEnd": f"{d}T23:59:59",
                "repeat": repeat,
                "repeatEnd": None,
                "days": [],
            },
        }
        for d in dates
    ]


def _rule(
    rule_id,
    name,
    *,
    pay_code="PublicHoliday",
    kind="Multiple",
    rate=1.5,
    conditions=(),
    **extra,
):
    return {
        "id": rule_id,
        "name": name,
        "shortName": None,
        "packageId": "pkg",
        "packageType": "Managed",
        "datestampDeleted": None,
        "rate": rate,
        "description": "",
        "payCode": pay_code,
        "type": kind,
        "evaluationType": "Any",
        "override": None,
        "payTypeRef": None,
        "roundUpHours": False,
        "linkedConditions": None,
        "conditions": list(conditions),
        "schedule": [],
        "actions": [],
        **extra,
    }


NZ = {
    "id": "b133e7ab-023e-42fb-909a-d9d2ed8fce11",
    "name": "New Zealand Public Holidays",
    "description": "Public holiday dates have been updated until 31 December 2026.",
    "type": "Managed",
    "activation": {
        "id": "a1",
        "packageId": "b133e7ab-023e-42fb-909a-d9d2ed8fce11",
        # 1 Oct 2025 07:00 NZDT
        "validFrom": "2025-09-30T18:00:00+00:00",
        "validTo": None,
        "enabledAt": "2025-10-27T21:40:42.6224894+00:00",
        "deletedAt": None,
        "type": "Managed",
    },
    "rules": [
        _rule(
            "r-labour",
            "Labour Day",
            conditions=_dated("r-labour", "2025-10-27", "2026-10-26"),
        ),
        _rule(
            "r-xmas",
            "Christmas Day",
            conditions=_dated("r-xmas", "2025-12-25", "2026-12-25"),
        ),
        # Before the package was activated: a holiday Loaded does not price.
        _rule(
            "r-easter",
            "Easter Monday",
            conditions=_dated("r-easter", "2025-04-21", "2026-04-06"),
        ),
    ],
}
CANTERBURY = {
    "id": "cant",
    "name": "Canterbury Anniversary Day",
    "description": "Currently accurate for 2022 through to and including 2026.",
    "type": "Managed",
    "activation": {
        "id": "a2",
        "packageId": "cant",
        # Activated for the 2025 day only: 14 Nov 07:00 → 15 Nov 07:00 NZDT.
        "validFrom": "2025-11-13T18:00:00+00:00",
        "validTo": "2025-11-14T18:00:00+00:00",
        "enabledAt": "2025-11-13T20:00:00+00:00",
        "deletedAt": None,
        "type": "Managed",
    },
    "rules": [
        _rule(
            "r-cant",
            "Canterbury Anniversary Day",
            conditions=[
                *_dated("r-cant", "2025-11-14", "2026-11-13"),
                {
                    "id": "em",
                    "deletedAt": None,
                    "ruleId": "r-cant",
                    "code": "EmployeeMatch",
                    "payload": {
                        "applyType": "All",
                        "matchConditions": [
                            {
                                "matchType": "StaffMember",
                                "operator": "!=",
                                "values": ["s1", "s2"],
                                "isManagedDefault": False,
                            }
                        ],
                        "includeSalariedEmployees": False,
                    },
                },
            ],
        )
    ],
}
OWN = {
    "id": "own",
    "name": "On costs",
    "description": None,
    "type": "UserDefined",
    "activation": {
        "id": "a3",
        "packageId": "own",
        "validFrom": "2025-11-22T05:35:15.87+00:00",
        "validTo": None,
        "enabledAt": "2025-11-22T05:35:37+00:00",
        "deletedAt": None,
        "type": "UserDefined",
    },
    "rules": [
        _rule(
            "r-ks",
            "KiwiSaver (3%)",
            pay_code="OnCost",
            kind="PercentageTotal",
            rate=3.0,
        ),
        # A dollar rate: payroll data this read never carries.
        _rule(
            "r-allow",
            "Late night allowance",
            pay_code="Allowance",
            kind="Add",
            rate=2.5,
        ),
    ],
}
LOADED = [NZ, CANTERBURY, OWN]


class FakeApi:
    def __init__(self, payload=None, *, shape=True):
        self.payload = copy.deepcopy(LOADED if payload is None else payload)
        self.shape = shape
        self.calls = []

    def call_api(self, connector, action, params):
        self.calls.append((connector, action, dict(params)))
        if isinstance(self.payload, dict):
            return self.payload
        if not self.shape:
            return self.payload
        return apply_response_transform(
            self.payload, SHAPE, venue_timezone="Pacific/Auckland"
        )


def run(api, **params):
    return harness.run(CODE, api, **{"venue": "Bessie & Royals", **params})


class TestGetPayAdjustments:
    def test_public_holidays_come_from_the_activated_packages_flagged_priced(self):
        api = FakeApi()
        out = run(api, start="2025-10-01", end="2026-12-31")
        assert api.calls == [
            (
                "loadedhub",
                "get_pay_adjustment_packages_raw",
                {"venue": "Bessie & Royals"},
            )
        ]
        assert [
            (h["date"], h["name"], h["priced"]) for h in out["public_holidays"]
        ] == [
            ("2025-10-27", "Labour Day", True),
            ("2025-11-14", "Canterbury Anniversary Day", True),
            ("2025-12-25", "Christmas Day", True),
            ("2026-04-06", "Easter Monday", True),
            ("2026-10-26", "Labour Day", True),
            # The Canterbury package's activation ended on 15 Nov 2025: still a
            # holiday, but Loaded will not pay the premium on it.
            ("2026-11-13", "Canterbury Anniversary Day", False),
            ("2026-12-25", "Christmas Day", True),
        ]
        first = out["public_holidays"][0]
        assert (
            first["weekday"] == "Mon"
            and first["package"] == "New Zealand Public Holidays"
        )
        assert out["dates_until"] == "2026-12-25"
        assert "1 of these public holidays" in out["note"]
        cant = next(
            p for p in out["packages"] if p["name"] == "Canterbury Anniversary Day"
        )
        assert (cant["kind"], cant["active_from"], cant["active_to"]) == (
            "managed",
            "2025-11-14",
            "2025-11-15",
        )
        assert cant["pay_adjustments"][0]["some_staff_only"] is True
        # The staff the condition names never reach the answer.
        assert "s1" not in json.dumps(out)

    def test_before_the_activation_a_holiday_is_listed_but_not_priced(self):
        out = run(FakeApi(), start="2025-04-01", end="2025-04-30")
        assert out["public_holidays"] == [
            {
                "date": "2025-04-21",
                "weekday": "Mon",
                "name": "Easter Monday",
                "package": "New Zealand Public Holidays",
                "priced": False,
            }
        ]

    def test_pay_adjustment_types_in_loadeds_words_and_only_factor_rates(self):
        out = run(FakeApi(), start="2026-01-01", end="2026-12-31")
        own = next(p for p in out["packages"] if p["name"] == "On costs")
        assert own["kind"] == "user-defined"
        by_name = {a["name"]: a for a in own["pay_adjustments"]}
        assert by_name["KiwiSaver (3%)"]["pay_adjustment_type"] == "On Cost"
        assert by_name["KiwiSaver (3%)"]["rate"] == 3.0
        assert by_name["Late night allowance"]["pay_adjustment_type"] == "Allowance"
        assert by_name["Late night allowance"]["rate"] is None
        assert by_name["Late night allowance"]["rate_type"] == "Add"
        nz = next(
            p for p in out["packages"] if p["name"] == "New Zealand Public Holidays"
        )
        labour = next(a for a in nz["pay_adjustments"] if a["name"] == "Labour Day")
        assert (
            labour["pay_adjustment_type"] == "Public Holiday"
            and labour["public_holiday"]
        )
        assert (labour["rate_type"], labour["rate"]) == ("Multiple", 1.5)
        assert labour["dates"] == ["2026-10-26"]

    def test_deleted_rules_and_dates_and_a_switched_off_rule(self):
        payload = copy.deepcopy(LOADED)
        rules = payload[0]["rules"]
        rules[0]["datestampDeleted"] = "2026-01-01T00:00:00+00:00"  # Labour Day gone
        rules[1]["conditions"][1]["deletedAt"] = (
            "2026-01-01T00:00:00+00:00"  # Xmas 2026 gone
        )
        rules[2]["override"] = {
            "id": "ov",
            "packageId": "x",
            "ruleId": "r-easter",
            "createdAt": "2026-01-01T00:00:00+00:00",
            "deletedAt": None,
        }
        rules.append(
            _rule(
                "r-weekly",
                "Repeating",
                conditions=_dated("r-weekly", "2026-03-02", repeat="Weekly"),
            )
        )
        out = run(FakeApi(payload), start="2026-01-01", end="2026-12-31")
        names = [(h["date"], h["name"], h["priced"]) for h in out["public_holidays"]]
        assert ("2026-10-26", "Labour Day", True) not in names
        assert not any(n == "Christmas Day" for _, n, _ in names)
        # Switched off for the company: listed, never priced.
        assert ("2026-04-06", "Easter Monday", False) in names
        assert not any(n == "Repeating" for _, n, _ in names)
        nz = next(
            p for p in out["packages"] if p["name"] == "New Zealand Public Holidays"
        )
        easter = next(a for a in nz["pay_adjustments"] if a["name"] == "Easter Monday")
        assert easter["switched_off"] is True

    def test_an_unshaped_payload_reads_the_same(self):
        shaped = run(FakeApi(), start="2025-10-01", end="2026-12-31")
        raw = run(FakeApi(shape=False), start="2025-10-01", end="2026-12-31")
        assert [(h["date"], h["priced"]) for h in raw["public_holidays"]] == [
            (h["date"], h["priced"]) for h in shaped["public_holidays"]
        ]

    def test_the_default_range_is_a_year_from_today_and_past_loadeds_dates_says_so(
        self,
    ):
        out = run(FakeApi())
        today = dt.datetime.now(dt.timezone.utc).date()
        assert out["start"] == today.isoformat()
        assert out["end"] == (today + dt.timedelta(days=364)).isoformat()
        far = run(FakeApi(), start="2027-01-01", end="2027-12-31")
        assert far["public_holidays"] == []
        assert "up to 2026-12-25" in far["note"]

    def test_refusals_and_a_failed_read(self):
        assert "venue is required" in harness.run(CODE, FakeApi())["error"]
        assert "start must be" in run(FakeApi(), start="soon")["error"]
        assert (
            "end is before start"
            in run(FakeApi(), start="2026-02-01", end="2026-01-01")["error"]
        )
        assert (
            "five years"
            in run(FakeApi(), start="2020-01-01", end="2026-01-02")["error"]
        )
        out = run(FakeApi({"error": "403 Forbidden"}), start="2026-01-01")
        assert out == {
            "error": "could not read the pay adjustments from Loaded: 403 Forbidden"
        }
