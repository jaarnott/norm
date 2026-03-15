"""
Seed data for the Norm prototype.
In-memory fixtures -- will move to Postgres.
"""

# ---------------------------------------------------------------------------
# Connector specs (config-driven connectors)
# ---------------------------------------------------------------------------

CONNECTOR_SPECS = [
    # ── BambooHR ── HR connector with two operations ──────────────────
    {
        "id": "cs1",
        "connector_name": "bamboohr",
        "display_name": "BambooHR",
        "category": "hr",
        "execution_mode": "template",
        "auth_type": "basic",
        "auth_config": {"username_field": "api_key", "password": "x"},
        "base_url_template": "https://{{ creds.subdomain }}.bamboohr.com/api/gateway.php/{{ creds.subdomain }}/v1",
        "credential_fields": [
            {"key": "subdomain", "label": "Subdomain", "secret": False},
            {"key": "api_key", "label": "API Key", "secret": True},
        ],
        "operations": [
            {
                "action": "create_employee",
                "description": "Create a new employee record in BambooHR",
                "method": "POST",
                "path_template": "/employees/",
                "headers": {"Accept": "application/json"},
                "required_fields": ["employee_name"],
                "field_mapping": {
                    "role": "jobTitle",
                    "start_date": "hireDate",
                    "email": "workEmail",
                    "phone": "mobilePhone",
                    "venue": "location",
                    "employment_type": "employmentHistoryStatus",
                },
                "request_body_template": '{"firstName": "{{ employee_name | split_name(\'first\') }}", "lastName": "{{ employee_name | split_name(\'last\') }}", "jobTitle": "{{ role | default_if_none }}", "hireDate": "{{ start_date | default_if_none }}", "workEmail": "{{ email | default_if_none }}", "mobilePhone": "{{ phone | default_if_none }}", "location": "{{ venue | flatten_venue }}", "employmentHistoryStatus": "{{ employment_type | default_if_none }}"}',
                "success_status_codes": [201],
                "response_ref_path": "headers.Location",
                "timeout_seconds": 30,
            },
            {
                "action": "terminate_employee",
                "description": "Terminate an existing employee in BambooHR",
                "method": "POST",
                "path_template": "/employees/{{ employee_id }}/terminationDetails",
                "headers": {"Accept": "application/json"},
                "required_fields": ["employee_name", "termination_date"],
                "field_mapping": {
                    "termination_date": "date",
                    "termination_reason": "terminationReason",
                },
                "request_body_template": '{"date": "{{ termination_date }}", "terminationReason": "{{ termination_reason | default_if_none }}"}',
                "success_status_codes": [200],
                "response_ref_path": None,
                "timeout_seconds": 30,
            },
        ],
        "example_requests": [],
        "api_documentation": None,
    },

    # ── Deputy ── Second HR connector (rostering / scheduling) ────────
    {
        "id": "cs2",
        "connector_name": "deputy",
        "display_name": "Deputy",
        "category": "hr",
        "execution_mode": "template",
        "auth_type": "bearer",
        "auth_config": {},
        "base_url_template": "https://{{ creds.install_url }}/api/v1",
        "credential_fields": [
            {"key": "install_url", "label": "Install URL (e.g. mycompany.na.deputy.com)", "secret": False},
            {"key": "access_token", "label": "Permanent Access Token", "secret": True},
        ],
        "operations": [
            {
                "action": "create_roster",
                "description": "Create a shift/roster entry for a staff member",
                "method": "POST",
                "path_template": "/resource/Roster",
                "headers": {"Content-Type": "application/json"},
                "required_fields": ["employee_name", "venue_name", "start_time", "end_time"],
                "field_mapping": {
                    "employee_name": "Employee",
                    "venue_name": "OperationalUnit",
                    "start_time": "StartTime",
                    "end_time": "EndTime",
                },
                "request_body_template": '{"Employee": "{{ employee_id }}", "OperationalUnit": "{{ venue_id }}", "StartTime": "{{ start_time }}", "EndTime": "{{ end_time }}", "Comment": "{{ notes | default_if_none }}"}',
                "success_status_codes": [200],
                "response_ref_path": "body.Id",
                "timeout_seconds": 15,
            },
            {
                "action": "list_rosters",
                "description": "List upcoming roster entries for a venue",
                "method": "GET",
                "path_template": "/resource/Roster",
                "headers": {},
                "required_fields": ["venue_name"],
                "field_mapping": {"venue_name": "OperationalUnit"},
                "request_body_template": None,
                "success_status_codes": [200],
                "response_ref_path": None,
                "timeout_seconds": 15,
            },
        ],
        "example_requests": [],
        "api_documentation": None,
    },

    # ── Bidfood ── Procurement connector ──────────────────────────────
    {
        "id": "cs3",
        "connector_name": "bidfood",
        "display_name": "Bidfood",
        "category": "procurement",
        "execution_mode": "template",
        "auth_type": "api_key_header",
        "auth_config": {"header_name": "X-API-Key"},
        "base_url_template": "https://api.bidfood.co.nz/v2",
        "credential_fields": [
            {"key": "api_key", "label": "API Key", "secret": True},
            {"key": "customer_code", "label": "Customer Code", "secret": False},
        ],
        "operations": [
            {
                "action": "create_order",
                "description": "Submit a purchase order to Bidfood",
                "method": "POST",
                "path_template": "/orders",
                "headers": {"Content-Type": "application/json"},
                "required_fields": ["product_name", "venue_name", "quantity"],
                "field_mapping": {
                    "product_name": "productCode",
                    "venue_name": "deliveryLocation",
                    "quantity": "qty",
                },
                "request_body_template": '{"customerCode": "{{ creds.customer_code }}", "deliveryLocation": "{{ venue_name }}", "lines": [{"productCode": "{{ product_code }}", "description": "{{ product_name }}", "qty": {{ quantity }}, "unit": "{{ unit | default(\'case\') }}"}], "notes": "{{ notes | default_if_none }}"}',
                "success_status_codes": [200, 201],
                "response_ref_path": "body.orderReference",
                "timeout_seconds": 30,
            },
            {
                "action": "check_stock",
                "description": "Check stock availability for a product",
                "method": "GET",
                "path_template": "/products/{{ product_code }}/availability",
                "headers": {},
                "required_fields": ["product_name"],
                "field_mapping": {"product_name": "productCode"},
                "request_body_template": None,
                "success_status_codes": [200],
                "response_ref_path": "body.available",
                "timeout_seconds": 15,
            },
        ],
        "example_requests": [],
        "api_documentation": None,
    },

    # ── LoadedHub ── Rostering / scheduling via OAuth2 ─────────────────
    {
        "id": "cs4",
        "connector_name": "loadedhub",
        "display_name": "LoadedHub",
        "category": "hr",
        "execution_mode": "template",
        "auth_type": "oauth2",
        "auth_config": {"token_field": "access_token"},
        "base_url_template": "https://{{ creds.loaded_domain }}/api",
        "oauth_config": {
            "authorize_url": "https://test.loadedhub.com/oauth/authorize",
            "token_url": "https://test.loadedhub.com/oauth/token",
            "scopes": "core:time:rw",
            "client_id": "",
            "client_secret": "",
        },
        "credential_fields": [
            {"key": "loaded_domain", "label": "LoadedHub Domain (e.g. test.loadedhub.com)", "secret": False},
        ],
        "operations": [
            {
                "action": "get_roster",
                "description": "Get a roster by date",
                "method": "GET",
                "path_template": "/time/rosters?searchDate={{ search_date }}",
                "headers": {"Content-Type": "application/json"},
                "required_fields": ["search_date"],
                "field_mapping": {
                    "search_date": "searchDate",
                },
                "request_body_template": None,
                "success_status_codes": [200],
                "response_ref_path": None,
                "timeout_seconds": 15,
            },
            {
                "action": "get_shifts",
                "description": "Get all shifts within a roster",
                "method": "GET",
                "path_template": "/time/rostered-shifts?rosterId={{ roster_id }}",
                "headers": {"Content-Type": "application/json"},
                "required_fields": ["roster_id"],
                "field_mapping": {
                    "roster_id": "rosterId",
                },
                "request_body_template": None,
                "success_status_codes": [200],
                "response_ref_path": None,
                "timeout_seconds": 15,
            },
            {
                "action": "create_shift",
                "description": "Create a new rostered shift",
                "method": "POST",
                "path_template": "/time/rostered-shifts",
                "headers": {"Content-Type": "application/json"},
                "required_fields": ["roster_id", "staff_member_id", "role_id", "clockin_time", "clockout_time"],
                "field_mapping": {
                    "roster_id": "rosterId",
                    "staff_member_id": "staffMemberId",
                    "role_id": "roleId",
                    "clockin_time": "clockinTime",
                    "clockout_time": "clockoutTime",
                    "breaks": "breaks",
                    "jobs": "jobs",
                    "hourly_rate": "adjustedHourlyRate",
                },
                "request_body_template": '{"rosterId": "{{ roster_id }}", "staffMemberId": "{{ staff_member_id }}", "roleId": "{{ role_id }}", "clockinTime": "{{ clockin_time }}", "clockoutTime": "{{ clockout_time }}", "breaks": {{ breaks | default("[]") }}, "jobs": {{ jobs | default("[]") }}, "adjustedHourlyRate": {{ hourly_rate | default("0") }}, "unpublished": false}',
                "success_status_codes": [201],
                "response_ref_path": "id",
                "timeout_seconds": 15,
            },
            {
                "action": "update_shift",
                "description": "Update an existing rostered shift",
                "method": "PUT",
                "path_template": "/time/rostered-shifts/{{ shift_id }}",
                "headers": {"Content-Type": "application/json"},
                "required_fields": ["shift_id", "roster_id", "staff_member_id", "role_id", "clockin_time", "clockout_time"],
                "field_mapping": {
                    "shift_id": "id",
                    "roster_id": "rosterId",
                    "staff_member_id": "staffMemberId",
                    "role_id": "roleId",
                    "clockin_time": "clockinTime",
                    "clockout_time": "clockoutTime",
                    "breaks": "breaks",
                    "jobs": "jobs",
                    "hourly_rate": "adjustedHourlyRate",
                },
                "request_body_template": '{"id": "{{ shift_id }}", "rosterId": "{{ roster_id }}", "staffMemberId": "{{ staff_member_id }}", "roleId": "{{ role_id }}", "clockinTime": "{{ clockin_time }}", "clockoutTime": "{{ clockout_time }}", "breaks": {{ breaks | default("[]") }}, "jobs": {{ jobs | default("[]") }}, "adjustedHourlyRate": {{ hourly_rate | default("0") }}, "unpublished": false, "datestampDeleted": null, "datestampLocked": null}',
                "success_status_codes": [200],
                "response_ref_path": "id",
                "timeout_seconds": 15,
            },
            {
                "action": "delete_shift",
                "description": "Soft-delete a rostered shift by setting datestampDeleted",
                "method": "PUT",
                "path_template": "/time/rostered-shifts/{{ shift_id }}",
                "headers": {"Content-Type": "application/json"},
                "required_fields": ["shift_id", "roster_id", "staff_member_id", "role_id", "clockin_time", "clockout_time"],
                "field_mapping": {
                    "shift_id": "id",
                    "roster_id": "rosterId",
                    "staff_member_id": "staffMemberId",
                    "role_id": "roleId",
                    "clockin_time": "clockinTime",
                    "clockout_time": "clockoutTime",
                    "breaks": "breaks",
                    "jobs": "jobs",
                    "hourly_rate": "adjustedHourlyRate",
                    "deleted_at": "datestampDeleted",
                },
                "request_body_template": '{"id": "{{ shift_id }}", "rosterId": "{{ roster_id }}", "staffMemberId": "{{ staff_member_id }}", "roleId": "{{ role_id }}", "clockinTime": "{{ clockin_time }}", "clockoutTime": "{{ clockout_time }}", "breaks": {{ breaks | default("[]") }}, "jobs": {{ jobs | default("[]") }}, "adjustedHourlyRate": {{ hourly_rate | default("0") }}, "unpublished": false, "datestampDeleted": "{{ deleted_at | default(now_utc) }}", "datestampLocked": null}',
                "success_status_codes": [200],
                "response_ref_path": "id",
                "timeout_seconds": 15,
            },
        ],
        "example_requests": [],
        "api_documentation": None,
    },
]


# ---------------------------------------------------------------------------
# Domain data
# ---------------------------------------------------------------------------

VENUES = [
    {"id": "v1", "name": "La Zeppa", "location": "Auckland CBD"},
    {"id": "v2", "name": "Mr Murdoch's", "location": "Auckland CBD"},
    {"id": "v3", "name": "Freeman & Grey", "location": "Auckland CBD"},
]

SUPPLIERS = [
    {"id": "s1", "name": "Bidfood"},
    {"id": "s2", "name": "Generic Supplier"},
]

PRODUCTS = [
    {
        "id": "p1",
        "name": "Jim Beam White Label Bourbon 700ml x 12",
        "supplier": "Bidfood",
        "category": "spirits",
        "unit": "case",
        "aliases": ["jim beam", "jb", "jim beam white", "jim beam white label"],
    },
    {
        "id": "p2",
        "name": "Corona Extra 330ml x 24",
        "supplier": "Bidfood",
        "category": "beer",
        "unit": "case",
        "aliases": ["corona", "corona extra", "coronas"],
    },
]
