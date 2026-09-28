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
"""

import copy
import pathlib

from app.connectors.function_executor import _SAFE_BUILTINS, _SAFE_MODULES

_DIR = pathlib.Path(__file__).resolve().parent.parent / "config" / "consolidators"
CODE = (_DIR / "get_hr.py").read_text()


def _emp(i, name, title, venue, company="Cook Brothers Bars Victoria Park Ltd"):
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
    def __init__(self):
        self.calls = []

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
        assert [e["name"] for e in run(Api(), location="goose")["employees"]] == [
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
            "rating": None,
        }
        assert out["more"] is True and "page 2" in out["note"]

    def test_a_job_id_implies_the_applications_view(self):
        api = Api()
        run(api, job_id=430)
        assert api.query_params() == [{"page": 1, "job_id": "430"}]

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

    def test_one_application_in_full_points_at_the_cv_reader(self):
        out = run(Api(), application_id=51240)
        assert out["applicant"]["name"] == "Glenn Osmond" and out["rating"] == 4
        assert out["questions"] == [{"q": "Right to work?", "a": "Yes"}]
        assert (
            out["resume"]["file_id"] == 57412
            and "get_applicant_resume" in out["resume"]["note"]
        )
        assert "cover_letter_file_id" not in out


def test_bad_view_refused_without_calls():
    api = Api()
    assert "view must be one of" in run(api, view="payroll")["error"]
    assert api.calls == []
