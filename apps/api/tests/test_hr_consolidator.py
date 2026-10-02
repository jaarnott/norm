"""get_hr — BambooHR employees, jobs and applications in one tool.

Exec'd under the REAL sandbox namespace against the canonical file. Fixtures
are shaped like the live account's payloads (28 Sep 2026): the employee
directory ({fields, employees: [{id, displayName, jobTitle, department,
location, workEmail, mobilePhone, …}]}), get_jobs (a bare list with
status/title/location objects and applicant counts), the applications page
({paginationComplete, applications, nextPageUrl}) and one application's
detail.

Facts pinned, each from the real account:
- "who works here" comes from the directory (95 current staff, each with
  their venue), never list_employees (page 1 of 1,504, 245 of 250 gone);
- employee detail asks BambooHR for named fields — the old row got
  {"id": "5"} — and drops BambooHR's "0000-00-00" non-dates;
- jobs default to open ones (12 of 162);
- application filters reach the query row as the parameters BambooHR
  accepted live (applicationStatus / applicationStatusId / searchString /
  jobStatusGroups / page).

And from the consolidator review (1 Oct 2026), each against the live account:
- Norm's "The Glass Goose" is BambooHR's "Glass Goose" and "Mr Murdoch's" its
  "Mr Murdochs" — names match after dropping case, punctuation and a leading
  "The", but a whole name must still match; no match lists the real names;
- a failed BambooHR read is reported as one (a failed statuses lookup used to
  say "no such status"), and a refused key (the 28 Sep 401) says an admin
  must re-enter it, so the agent stops falling back to another system;
- division (Kitchen 27 / Front of House 40 / Management 17) is searched;
- a job's candidates default to active ones, and `filters` says so;
- the CV reader folded in: application_id + cv true returns the CV as a
  document block in the same call.
"""

import copy
import pathlib

from app.connectors.function_executor import _SAFE_BUILTINS, _SAFE_MODULES

_DIR = pathlib.Path(__file__).resolve().parent.parent / "config" / "consolidators"
CODE = (_DIR / "get_hr.py").read_text()


def _emp(
    i,
    name,
    title,
    venue,
    company="Cook Brothers Bars Victoria Park Ltd",
    division="Kitchen",
):
    first, last = name.split(" ", 1)
    return {
        "id": str(i),
        "displayName": name,
        "firstName": first,
        "lastName": last,
        "preferredName": None,
        "jobTitle": title,
        "department": company,
        "location": venue,
        "division": division,
        "workEmail": None,
        "mobilePhone": "021000000",
        "photoUrl": "https://x/huge-signed-url",
    }


DIRECTORY = {
    "fields": [{"id": "displayName"}],
    "employees": [
        _emp(1502, "Rendi Agung", "Chef de Partie", "La Zeppa"),
        _emp(1400, "Felipe Araya", "Head Chef", "La Zeppa"),
        _emp(
            1300,
            "Sam Lee",
            "Bartender",
            "Glass Goose",
            "Cook Brothers Bars Federal St Ltd",
            "Front of House",
        ),
    ],
}
DETAIL = {
    "id": "1502",
    "displayName": "Rendi Agung",
    "firstName": "Rendi",
    "lastName": "Agung",
    "preferredName": None,
    "jobTitle": "Chef de Partie",
    "location": "La Zeppa",
    "division": "Kitchen",
    "status": "Active",
    "employmentHistoryStatus": "Full-Time",
    "hireDate": "2025-10-11",
    "terminationDate": "0000-00-00",
    "workEmail": None,
    "supervisor": "Araya, Felipe",
}


def _job(i, title, status, venue, posted, total=3):
    return {
        "id": i,
        "title": {"id": None, "label": title},
        "postedDate": posted,
        "location": {
            "id": 1,
            "label": "Auckland, Auckland",
            "address": {"name": venue},
        },
        "department": {"id": 2, "label": "Co"},
        "status": {"id": 1, "label": status},
        "hiringLead": {
            "employeeId": 3,
            "firstName": "George",
            "lastName": "Eason",
            "avatar": "https://x",
        },
        "totalApplicantsCount": total,
        "activeApplicantsCount": total - 1,
        "newApplicantsCount": 1,
        "postingUrl": "https://x",
    }


JOBS = [
    _job(
        535,
        "Glass Goose - Sous Chef - Full Time",
        "Open",
        "Glass Goose",
        "2026-09-15T03:26:09+00:00",
    ),
    _job(
        430,
        "Mr Murdoch's - Bar Team - Part Time",
        "Open",
        "Mr Murdochs",
        "2026-08-01T00:00:00+00:00",
    ),
    _job(100, "La Zeppa - Chef", "Filled", "La Zeppa", "2025-01-01T00:00:00+00:00"),
    _job(90, "Old role", "On Hold", "Glass Goose", "2024-01-01T00:00:00+00:00"),
]
APPS_PAGE = {
    "paginationComplete": False,
    "nextPageUrl": "https://x?page=2",
    "applications": [
        {
            "id": 51240,
            "appliedDate": "2026-09-28T03:36:21+00:00",
            "status": {"id": 1, "label": "New"},
            "rating": None,
            "applicant": {
                "id": 1,
                "firstName": "Glenn",
                "lastName": "Osmond",
                "email": "g@x.nz",
                "avatar": "https://x",
                "source": "bamboohr",
            },
            "job": {
                "id": 430,
                "title": {"id": None, "label": "Mr Murdoch's - Bar Team - Part Time"},
            },
        },
    ],
}
APP_DETAIL = {
    "id": 51240,
    "appliedDate": "2026-09-28T03:36:21+00:00",
    "status": {
        "id": 1,
        "label": "New",
        "dateChanged": "2026-09-28T03:36:21+00:00",
        "changedByUser": None,
    },
    "rating": 4,
    "applicant": {
        "id": 1,
        "firstName": "Glenn",
        "lastName": "Osmond",
        "email": "g@x.nz",
        "phoneNumber": "+64 22",
        "source": "bamboohr",
        "availableStartDate": None,
        "linkedinUrl": None,
    },
    "job": {
        "id": 430,
        "title": {"id": None, "label": "Mr Murdoch's - Bar Team - Part Time"},
        "hiringLead": {"firstName": "George", "lastName": "Eason"},
    },
    "desiredSalary": "25",
    "questionsAndAnswers": [
        {"question": {"label": "Right to work?"}, "answer": {"label": "Yes"}}
    ],
    "commentCount": 0,
    "emailCount": 1,
    "attachmentCount": 1,
    "alsoConsideredForCount": 0,
    "resumeFileId": 57412,
    "coverLetterFileId": None,
}
CV_FILE = {
    "content_base64": "JVBERi0xLjQ=",
    "content_type": "application/pdf",
    "size_bytes": 91659,
}
#: What a refused key looks like from the engine (prod, 28 Sep 2026).
UNAUTHORISED = {
    "error": "API error 401: <html>\n<head><title>401 Authorization Required"
    "</title></head>\n<body><center><h1>401 Authorization Required</h1>"
    "</center></body></html>"
}
STATUSES = [
    {"id": "1", "code": "NEW", "name": "New", "enabled": True},
    {"id": "2", "code": "REVIEWED", "name": "Reviewed", "enabled": True},
    {
        "id": "3",
        "code": "SCHEDPHONE",
        "name": "Schedule Phone Screen",
        "enabled": False,
    },
]


class Api:
    def __init__(self, **overrides):
        self.calls = []
        self.overrides = overrides

    def call_api(self, connector, action, params=None):
        p = dict(params or {})
        self.calls.append((action, p))
        table = {
            "get_employee_directory": DIRECTORY,
            "get_employee_detail": DETAIL,
            "get_jobs": JOBS,
            "get_applications_query": APPS_PAGE,
            "get_application_details": APP_DETAIL,
            "get_applicant_statuses": STATUSES,
            "download_file": CV_FILE,
            **self.overrides,
        }
        if action not in table:
            raise AssertionError(f"unexpected action {action}")
        return copy.deepcopy(table[action])

    def query_params(self):
        return [p for a, p in self.calls if a == "get_applications_query"]


def run(api, **params):
    ns = {"__builtins__": _SAFE_BUILTINS, **_SAFE_MODULES}
    exec(CODE, ns)
    return ns["run"](dict(params), api.call_api, lambda m: None)


class TestEmployees:
    def test_default_is_current_staff_from_the_directory_by_venue(self):
        api = Api()
        out = run(api)
        assert out["current_staff"] == 3
        assert out["by_venue"] == {"Glass Goose": 1, "La Zeppa": 2}
        assert out["employees"][0] == {
            "id": "1300",
            "name": "Sam Lee",
            "preferred": None,
            "job_title": "Bartender",
            "venue": "Glass Goose",
            "division": "Front of House",
            "company": "Cook Brothers Bars Federal St Ltd",
            "work_email": None,
            "mobile": "021000000",
        }
        assert "photoUrl" not in str(out)
        assert [a for a, _ in api.calls] == ["get_employee_directory"]

    def test_query_matches_name_or_role_and_location_filters_venue(self):
        assert [e["name"] for e in run(Api(), query="chef")["employees"]] == [
            "Felipe Araya",
            "Rendi Agung",
        ]
        assert [e["name"] for e in run(Api(), location="Glass Goose")["employees"]] == [
            "Sam Lee"
        ]

    def test_employee_detail_asks_for_fields_and_drops_non_dates(self):
        api = Api()
        out = run(api, employee_id=1502)
        assert api.calls == [("get_employee_detail", {"employee_id": "1502"})]
        assert out["jobTitle"] == "Chef de Partie" and out["hireDate"] == "2025-10-11"
        assert "terminationDate" not in out and "workEmail" not in out


class TestJobs:
    def test_open_jobs_by_default_newest_first_with_counts(self):
        out = run(Api(), view="jobs")
        assert [j["id"] for j in out["jobs"]] == [535, 430]
        assert out["jobs_by_status"] == {"Open": 2, "Filled": 1, "On Hold": 1}
        assert out["jobs"][0] == {
            "id": 535,
            "title": "Glass Goose - Sous Chef - Full Time",
            "venue": "Glass Goose",
            "status": "Open",
            "posted": "2026-09-15",
            "hiring_lead": "George Eason",
            "applicants": {"total": 3, "active": 2, "new": 1},
        }

    def test_status_all_and_location(self):
        out = run(Api(), view="jobs", status="all", location="la zeppa")
        assert [j["id"] for j in out["jobs"]] == [100]

    def test_bad_status_refused(self):
        assert (
            "job status must be one of"
            in run(Api(), view="jobs", status="closed")["error"]
        )


class TestApplications:
    def test_default_is_active_applications_on_open_jobs(self):
        api = Api()
        out = run(api, view="applications")
        assert api.query_params() == [
            {"page": 1, "status_group": "ALL_ACTIVE", "job_status": "DRAFT_AND_OPEN"}
        ]
        assert out["applications"][0] == {
            "id": 51240,
            "applied": "2026-09-28",
            "status": "New",
            "name": "Glenn Osmond",
            "email": "g@x.nz",
            "job": "Mr Murdoch's - Bar Team - Part Time",
            "job_id": 430,
            "rating": None,
        }
        assert out["more"] is True and "page 2" in out["note"]

    def test_a_job_id_implies_the_applications_view(self):
        api = Api()
        out = run(api, job_id=430)
        # BambooHR's own default (active only) is now explicit and visible.
        assert api.query_params() == [
            {"page": 1, "job_id": "430", "status_group": "ALL_ACTIVE"}
        ]
        assert out["filters"]["status_group"] == "ALL_ACTIVE"
        assert "active candidates only" in out["note"]

    def test_a_named_status_resolves_to_its_id(self):
        api = Api()
        run(api, view="applications", status="reviewed")
        assert api.query_params()[0] == {
            "page": 1,
            "status_id": "2",
            "job_status": "DRAFT_AND_OPEN",
        }

    def test_an_unknown_status_lists_the_enabled_ones(self):
        out = run(Api(), view="applications", status="shortlisted")
        assert "No application status 'shortlisted'" in out["error"]
        assert (
            "Reviewed" in out["error"] and "Schedule Phone Screen" not in out["error"]
        )

    def test_a_name_search_looks_across_every_job(self):
        api = Api()
        run(api, view="applications", query="osmond", page=2)
        assert api.query_params() == [
            {"page": 2, "search": "osmond", "job_status": "ALL"}
        ]

    def test_hired_widens_to_all_jobs(self):
        api = Api()
        run(api, view="applications", status="hired")
        assert (
            api.query_params()[0]["job_status"] == "ALL"
            and api.query_params()[0]["status_group"] == "HIRED"
        )

    def test_one_application_in_full_says_how_to_read_the_cv(self):
        api = Api()
        out = run(api, application_id=51240)
        assert out["applicant"]["name"] == "Glenn Osmond" and out["rating"] == 4
        assert out["questions"] == [{"q": "Right to work?", "a": "Yes"}]
        assert out["job_id"] == 430
        assert "cv true" in out["cv"] and "_document" not in out
        assert "cover_letter_file_id" not in out
        assert [a for a, _ in api.calls] == ["get_application_details"]


def test_bad_view_refused_without_calls():
    api = Api()
    assert "view must be one of" in run(api, view="payroll")["error"]
    assert api.calls == []


class TestVenueNames:
    """Norm's venue names against BambooHR's (live, 1 Oct 2026)."""

    def test_the_glass_goose_is_glass_goose(self):
        out = run(Api(), location="The Glass Goose")
        assert [e["name"] for e in out["employees"]] == ["Sam Lee"]

    def test_apostrophes_and_case_do_not_matter(self):
        out = run(Api(), view="jobs", location="mr murdoch's")
        assert [j["id"] for j in out["jobs"]] == [430]

    def test_a_partial_name_is_not_a_match_and_lists_the_venues(self):
        api = Api()
        out = run(api, location="Goose")
        assert out["employees"] == [] and out["current_staff"] == 0
        assert "No venue called 'Goose'" in out["note"]
        assert "Glass Goose, La Zeppa" in out["note"]

    def test_jobs_with_an_unknown_venue_list_the_venues(self):
        out = run(Api(), view="jobs", location="Freeman & Grey")
        assert out["jobs"] == [] and "No venue called 'Freeman & Grey'" in out["note"]
        assert "Mr Murdochs" in out["note"]

    def test_a_known_venue_with_no_open_jobs_is_just_empty(self):
        out = run(Api(), view="jobs", location="La Zeppa")
        assert out["jobs"] == [] and "note" not in out


class TestFailuresAreBambooHRs:
    def test_a_refused_key_says_an_admin_must_re_enter_it(self):
        out = run(Api(get_employee_directory=UNAUTHORISED))
        assert "refused Norm's API key" in out["error"]
        assert "re-enter the BambooHR key" in out["error"]
        assert "don't answer from another system" in out["error"]
        assert "<html>" not in out["error"] and "<title>" not in out["error"]

    def test_a_failed_statuses_lookup_is_not_an_unknown_status(self):
        out = run(
            Api(get_applicant_statuses={"error": "API error 500: boom"}),
            view="applications",
            status="reviewed",
        )
        assert "No application status" not in out["error"]
        assert "BambooHR failure" in out["error"] and "500" in out["error"]

    def test_failed_jobs_and_applications_reads_say_so(self):
        out = run(Api(get_jobs=UNAUTHORISED), view="jobs")
        assert "refused Norm's API key" in out["error"]
        out = run(Api(get_applications_query={"error": "timeout"}), view="applications")
        assert "Couldn't read applications from BambooHR" in out["error"]

    def test_a_missing_employee_or_application_is_not_a_failure(self):
        out = run(
            Api(get_employee_detail={"error": "API error 404: Not Found"}),
            employee_id=9,
        )
        assert out["error"] == "No employee 9 in BambooHR."
        out = run(
            Api(get_application_details={"error": "API error 404: Not Found"}),
            application_id=9,
        )
        assert out["error"] == "No application 9 in BambooHR."

    def test_a_bad_job_status_is_refused_before_calling(self):
        api = Api()
        assert "job status must be one of" in run(api, view="jobs", status="x")["error"]
        assert api.calls == []


class TestSmallFixes:
    def test_division_is_searched(self):
        out = run(Api(), query="kitchen")
        assert [e["name"] for e in out["employees"]] == ["Felipe Araya", "Rendi Agung"]

    def test_limit_is_capped_at_100(self):
        many = [
            _job(i, f"Role {i}", "Open", "La Zeppa", "2026-09-01T00:00:00+00:00")
            for i in range(150)
        ]
        out = run(Api(get_jobs=many), view="jobs", limit=500)
        assert len(out["jobs"]) == 100 and out["total"] == 150

    def test_text_numbers_are_numbers(self):
        out = run(Api(), view="jobs", limit="1")
        assert len(out["jobs"]) == 1


class TestCv:
    def test_cv_true_returns_the_application_and_the_document(self):
        api = Api()
        out = run(api, application_id=51240, cv=True)
        assert [a for a, _ in api.calls] == ["get_application_details", "download_file"]
        assert api.calls[1][1] == {"file_id": "57412"}
        assert out["applicant"]["name"] == "Glenn Osmond"
        assert out["cv"] == {
            "file_id": 57412,
            "content_type": "application/pdf",
            "size_bytes": 91659,
        }
        assert out["_document"] == {
            "type": "document",
            "source": {
                "type": "base64",
                "media_type": "application/pdf",
                "data": "JVBERi0xLjQ=",
            },
        }

    def test_cv_sent_as_text_still_counts(self):
        assert "_document" in run(Api(), application_id=51240, cv="true")
        assert "_document" not in run(Api(), application_id=51240, cv="false")

    def test_a_word_cv_keeps_its_type_without_the_charset(self):
        word = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
        out = run(
            Api(
                download_file={
                    "content_base64": "UEsDBA==",
                    "content_type": word + "; charset=binary",
                    "size_bytes": 20000,
                }
            ),
            application_id=51240,
            cv=True,
        )
        assert out["_document"]["source"]["media_type"] == word

    def test_no_cv_on_file_downloads_nothing(self):
        api = Api(get_application_details={**APP_DETAIL, "resumeFileId": None})
        out = run(api, application_id=51240, cv=True)
        assert out["cv"] == "none on file" and "_document" not in out
        assert [a for a, _ in api.calls] == ["get_application_details"]

    def test_a_failed_download_still_returns_the_application(self):
        out = run(Api(download_file=UNAUTHORISED), application_id=51240, cv=True)
        assert out["applicant"]["name"] == "Glenn Osmond"
        assert "refused Norm's API key" in out["cv"] and "_document" not in out

    def test_cv_needs_an_application(self):
        api = Api()
        assert (
            "cv needs an application_id"
            in run(api, cv=True, view="applications")["error"]
        )
        assert api.calls == []
