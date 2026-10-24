# ruff: noqa: F821 — `datetime` and `json` are injected into the sandbox
# namespace by app/connectors/function_executor.py; they are not imports.
#
# Canonical function_code for `loadedhub.get_pay_adjustments` (installed by
# scripts/sync_payroll_config.py; claimed by the Payroll App).
#
# Loaded's pay adjustments for one venue's company, in Loaded's own words:
# a pay adjustment PACKAGE (the managed "New Zealand Public Holidays", a
# regional "Canterbury Anniversary Day", a company's own package) holds pay
# ADJUSTMENTS ("Labour Day", time and a half), and each adjustment has a pay
# adjustment TYPE (Loaded's payCode: Award, Allowance, Public Holiday,
# Overtime, On Cost, Other). A public holiday is an adjustment of type Public
# Holiday; its dates are the adjustment's ScheduleMatch conditions — one per
# year, venue-local wall clock with no offset (live, 23 Oct 2026: La Zeppa,
# Bessie & Royals, Mr Murdochs). Loaded's own roster reads public holidays
# exactly this way (rostering/…/roster/public-holidays.ts).
#
# Rules, and why:
#   - only the packages the company has activated (the endpoint's
#     includeActivatedPackagesOnly=true) — that is the region: a venue in
#     Christchurch activates Canterbury Anniversary Day, one in Auckland may
#     not activate Auckland's, and Norm takes Loaded's word for it;
#   - every dated occurrence of a Public Holiday adjustment is a public
#     holiday; `priced` says whether Loaded will actually apply the
#     adjustment on that date — inside the package's activation window and
#     not switched off for the company (a rule override). A holiday outside
#     the window is still a holiday (the forecast wants it); a payroll person
#     wants to know Loaded won't pay the premium;
#   - a deleted adjustment or condition is skipped;
#   - a rate is shown only when it is a multiplier or a percentage (time and
#     a half, KiwiSaver 3%): an Add / Equals adjustment's rate is a dollar
#     pay rate, which is payroll data this read does not carry;
#   - `dates_until` is the last public holiday Loaded holds at all — its
#     managed package is "updated until 31 December 2026", so a range past it
#     is short of holidays until Loaded publishes the next year's dates.
#
# Reads get_pay_adjustment_packages_raw once (GET /pay-rules/packages, Loaded
# scope core:pay-packages:r).
#
# Requires consolidator_config: {"max_api_calls": 2}

_TYPE_LABELS = {
    "Award": "Award",
    "Allowance": "Allowance",
    "PublicHoliday": "Public Holiday",
    "Overtime": "Overtime",
    "OnCost": "On Cost",
    "Other": "Other",
    "NoExport": "No Export",
}
#: Rule types whose rate is a factor, not a dollar amount.
_FACTOR_TYPES = ("Multiple", "PercentageTotal")
_DOW = ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")
_DEFAULT_DAYS = 365
_MAX_DAYS = 5 * 366


def _rows_of(payload):
    if isinstance(payload, list):
        return [r for r in payload if isinstance(r, dict)]
    if isinstance(payload, dict):
        for value in payload.values():
            if isinstance(value, list) and (not value or isinstance(value[0], dict)):
                return [r for r in value if isinstance(r, dict)]
    return []


def _date(text):
    try:
        return datetime.date.fromisoformat(str(text)[:10])
    except (TypeError, ValueError):
        return None


def _wall(text):
    """A naive venue-local datetime. The schedule stamps are wall clock with
    no offset; the activation instants arrive converted to the venue's zone
    by the shape (`|tz`), so their offset is dropped after it."""
    if not text:
        return None
    s = str(text).strip()
    if s.endswith("Z"):
        s = s[:-1] + "+00:00"
    try:
        moment = datetime.datetime.fromisoformat(s)
    except ValueError:
        return None
    return moment.replace(tzinfo=None)


def _occurrences(rule):
    """(start, end) wall-clock pairs from a rule's ScheduleMatch conditions
    (and its legacy schedule rows, which older managed rules still carry)."""
    out = []
    for c in rule.get("conditions") or []:
        if not isinstance(c, dict) or c.get("deletedAt"):
            continue
        if c.get("code") != "ScheduleMatch":
            continue
        p = c.get("payload") if isinstance(c.get("payload"), dict) else c
        if str(p.get("repeat") or "Never") != "Never":
            continue  # Loaded's managed dates are concrete; a repeat is not a holiday
        start = _wall(p.get("datestampStart"))
        if start is not None:
            out.append((start, _wall(p.get("datestampEnd")) or start))
    for s in rule.get("schedule") or []:
        if not isinstance(s, dict) or str(s.get("repeat") or "Never") != "Never":
            continue
        start = _wall(s.get("datestampStart"))
        if start is not None:
            out.append((start, _wall(s.get("datestampEnd")) or start))
    return out


def _staff_limited(rule):
    return any(
        isinstance(c, dict)
        and not c.get("deletedAt")
        and c.get("code") == "EmployeeMatch"
        for c in rule.get("conditions") or []
    )


def _switched_off(rule):
    """A live rule override switches the adjustment off for the company
    (Loaded's RuleService skips an overridden rule)."""
    override = rule.get("override")
    if isinstance(override, dict):
        return not override.get("deletedAt")
    return bool(rule.get("overrideId")) and not rule.get("overrideDeletedAt")


def _activation(pkg):
    """(valid_from, valid_to, deleted) — from the shaped row (activeFrom /
    activeTo converted to the venue's zone) or a raw one (activation{})."""
    act = pkg.get("activation")
    if isinstance(act, dict):
        return (
            _wall(act.get("validFrom")),
            _wall(act.get("validTo")),
            bool(act.get("deletedAt")),
        )
    return (
        _wall(pkg.get("activeFrom")),
        _wall(pkg.get("activeTo")),
        bool(pkg.get("activationDeletedAt")),
    )


def run(params, call_api, log):
    venue = str(params.get("venue") or params.get("venue_name") or "").strip()
    if not venue and not params.get("venue_id"):
        return {"error": "venue is required: the venue's name"}
    start = _date(params.get("start")) if params.get("start") else None
    end = _date(params.get("end")) if params.get("end") else None
    if params.get("start") and start is None:
        return {"error": "start must be YYYY-MM-DD"}
    if params.get("end") and end is None:
        return {"error": "end must be YYYY-MM-DD"}
    if start is None:
        # date.today() dies in the sandbox (it imports `time` lazily); the
        # UTC date is at most a day early, and callers wanting an exact day
        # pass start from norm.resolve_dates.
        start = datetime.datetime.now(datetime.timezone.utc).date()
    if end is None:
        end = start + datetime.timedelta(days=_DEFAULT_DAYS - 1)
    if end < start:
        return {"error": "end is before start"}
    if (end - start).days > _MAX_DAYS:
        return {"error": "at most five years per call"}

    venue_arg = {"venue": venue} if venue else {}
    if params.get("venue_id"):
        venue_arg["venue_id"] = params["venue_id"]
    raw = call_api("loadedhub", "get_pay_adjustment_packages_raw", venue_arg)
    if isinstance(raw, dict) and raw.get("error"):
        return {
            "error": "could not read the pay adjustments from Loaded: "
            + str(raw["error"])
        }

    packages = []
    holidays = []
    last_holiday = None
    for pkg in _rows_of(raw):
        valid_from, valid_to, gone = _activation(pkg)
        if gone:
            continue
        name = str(pkg.get("name") or "").strip()
        adjustments = []
        for rule in pkg.get("rules") or []:
            if not isinstance(rule, dict) or rule.get("datestampDeleted"):
                continue
            pay_code = str(rule.get("payCode") or "")
            is_holiday = pay_code == "PublicHoliday"
            rate_type = rule.get("type")
            off = _switched_off(rule)
            dates = []
            for occ_start, occ_end in sorted(_occurrences(rule)):
                day = occ_start.date()
                if is_holiday and (last_holiday is None or day > last_holiday):
                    last_holiday = day
                if not (start <= day <= end):
                    continue
                dates.append(day.isoformat())
                if is_holiday:
                    priced = (
                        not off
                        and (valid_from is None or occ_end >= valid_from)
                        and (valid_to is None or occ_start < valid_to)
                    )
                    holidays.append(
                        {
                            "date": day.isoformat(),
                            "weekday": _DOW[day.weekday()],
                            "name": str(rule.get("name") or "").strip(),
                            "package": name,
                            "priced": priced,
                        }
                    )
            adjustments.append(
                {
                    "name": str(rule.get("name") or "").strip(),
                    "pay_adjustment_type": _TYPE_LABELS.get(pay_code, pay_code or None),
                    "public_holiday": is_holiday,
                    "rate_type": rate_type,
                    "rate": rule.get("rate") if rate_type in _FACTOR_TYPES else None,
                    "some_staff_only": _staff_limited(rule),
                    "switched_off": off,
                    "dates": dates,
                }
            )
        packages.append(
            {
                "name": name,
                "kind": "managed" if pkg.get("type") == "Managed" else "user-defined",
                "description": str(pkg.get("description") or "").strip() or None,
                "active_from": valid_from.date().isoformat() if valid_from else None,
                "active_to": valid_to.date().isoformat() if valid_to else None,
                "pay_adjustments": adjustments,
            }
        )

    holidays.sort(key=lambda h: (h["date"], h["name"]))
    out = {
        "venue": venue or None,
        "start": start.isoformat(),
        "end": end.isoformat(),
        "public_holidays": holidays,
        "packages": packages,
        "dates_until": last_holiday.isoformat() if last_holiday else None,
    }
    notes = []
    if last_holiday is not None and last_holiday < end:
        notes.append(
            "Loaded holds public holiday dates up to "
            + last_holiday.isoformat()
            + "; later holidays appear once Loaded publishes them."
        )
    elif last_holiday is None:
        notes.append("No activated package has a public holiday adjustment.")
    unpriced = [h for h in holidays if not h["priced"]]
    if unpriced:
        notes.append(
            str(len(unpriced))
            + " of these public holidays fall outside their package's activation "
            "or are switched off, so Loaded will not apply the adjustment on them."
        )
    if notes:
        out["note"] = " ".join(notes)
    return out
