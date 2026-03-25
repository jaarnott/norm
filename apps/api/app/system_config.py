"""Declarative system configuration for connector specs, agents, and bindings.

These definitions are the source of truth for system-level configuration.
They are synced to the database on every deploy (app startup) so that code
changes to connector specs, agent definitions, and bindings are always
reflected in production without manual API calls.

User/environment configuration (credentials, custom prompts, enabled flags
set by admins) is never overwritten by the sync.
"""

# ---------------------------------------------------------------------------
# Connector Specs — define what integrations exist and their available tools
# ---------------------------------------------------------------------------

CONNECTOR_SPECS: list[dict] = [
    {
        "connector_name": "norm",
        "display_name": "Norm",
        "category": "internal",
        "execution_mode": "internal",
        "auth_type": "none",
        "tools": [
            {
                "action": "search_tool_result",
                "method": "GET",
                "description": (
                    "Search through a previous tool call's full result by keyword. "
                    "Use when a result was too large or slimmed and you need to "
                    "find specific items."
                ),
                "required_fields": ["tool_call_id", "query"],
                "optional_fields": ["fields"],
                "field_descriptions": {
                    "tool_call_id": "The _tool_call_id from the slimmed/large result",
                    "query": "Search keyword (case-insensitive match across all field values)",
                    "fields": "Optional: comma-separated field names to return. Omit for all fields.",
                },
            },
        ],
    },
    {
        "connector_name": "norm_reports",
        "display_name": "Norm Reports",
        "category": "reports",
        "execution_mode": "internal",
        "auth_type": "none",
        "tools": [
            {
                "action": "render_chart",
                "method": "GET",
                "description": (
                    "Render data as a visual chart by referencing a prior tool call."
                ),
                "required_fields": [
                    "title",
                    "chart_type",
                    "x_axis_key",
                    "series",
                    "source_tool_call_id",
                ],
                "optional_fields": [
                    "x_axis_label",
                    "orientation",
                    "select_fields",
                    "field_labels",
                ],
                "field_descriptions": {
                    "source_tool_call_id": (
                        "The tool_use ID of the GET tool call whose data to visualize."
                    ),
                    "title": "Chart title",
                    "chart_type": "bar, line, pie, stacked_bar, scatter, bubble, or table",
                    "x_axis_key": "Field name from the data for x-axis",
                    "series": "Array of {key, label} objects for data series to plot",
                    "select_fields": "Array of field names to include from the raw data",
                    "field_labels": "Object mapping raw field names to display labels",
                },
                "field_schema": {
                    "series": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "key": {"type": "string"},
                                "label": {"type": "string"},
                            },
                        },
                    },
                    "select_fields": {
                        "type": "array",
                        "items": {"type": "string"},
                    },
                    "field_labels": {
                        "type": "object",
                    },
                },
                "display_component": "chart",
            },
        ],
    },
    {
        "connector_name": "bamboohr",
        "display_name": "BambooHR",
        "category": "hr",
        "execution_mode": "template",
        "base_url_template": "https://{{ creds.subdomain }}.bamboohr.com/api/gateway.php/{{ creds.subdomain }}/v1",
        "auth_type": "basic",
        "auth_config": {
            "username_field": "api_key",
            "password": "x",
        },
        "tools": [
            {
                "action": "create_employee",
                "description": "Create a new employee record in BambooHR",
                "method": "POST",
                "path_template": "/employees/",
                "headers": {
                    "Accept": "application/json",
                },
                "required_fields": [
                    "employee_name",
                ],
                "field_mapping": {
                    "role": "jobTitle",
                    "start_date": "hireDate",
                    "email": "workEmail",
                    "phone": "mobilePhone",
                    "venue": "location",
                    "employment_type": "employmentHistoryStatus",
                },
                "request_body_template": '{"firstName": "{{ employee_name | split_name(\'first\') }}", "lastName": "{{ employee_name | split_name(\'last\') }}", "jobTitle": "{{ role | default_if_none }}", "hireDate": "{{ start_date | default_if_none }}", "workEmail": "{{ email | default_if_none }}", "mobilePhone": "{{ phone | default_if_none }}", "location": "{{ venue | flatten_venue }}", "employmentHistoryStatus": "{{ employment_type | default_if_none }}"}',
                "success_status_codes": [
                    201,
                ],
                "response_ref_path": "headers.Location",
                "timeout_seconds": 30,
            },
            {
                "action": "terminate_employee",
                "description": "Terminate an existing employee in BambooHR",
                "method": "POST",
                "path_template": "/employees/{{ employee_id }}/terminationDetails",
                "headers": {
                    "Accept": "application/json",
                },
                "required_fields": [
                    "employee_name",
                    "termination_date",
                ],
                "field_mapping": {
                    "termination_date": "date",
                    "termination_reason": "terminationReason",
                },
                "request_body_template": '{"date": "{{ termination_date }}", "terminationReason": "{{ termination_reason | default_if_none }}"}',
                "success_status_codes": [
                    200,
                ],
                "response_ref_path": None,
                "timeout_seconds": 30,
            },
            {
                "action": "get_jobs",
                "method": "GET",
                "path_template": "/applicant_tracking/jobs",
                "headers": {
                    "Accept": "application/json",
                },
                "required_fields": [],
                "field_mapping": {},
                "success_status_codes": [
                    200,
                ],
                "timeout_seconds": 30,
            },
            {
                "action": "get_applications",
                "method": "GET",
                "path_template": "/applicant_tracking/applications{% if job_id %}?jobId={{ job_id }}{% endif %}",
                "headers": {
                    "Accept": "application/json",
                },
                "required_fields": [],
                "field_mapping": {},
                "success_status_codes": [
                    200,
                ],
                "timeout_seconds": 30,
                "field_descriptions": {
                    "job_id": "BambooHR job ID to filter applications (optional)",
                },
            },
            {
                "action": "get_application_details",
                "method": "GET",
                "path_template": "/applicant_tracking/applications/{{ application_id }}",
                "headers": {
                    "Accept": "application/json",
                },
                "required_fields": [
                    "application_id",
                ],
                "field_mapping": {},
                "success_status_codes": [
                    200,
                ],
                "timeout_seconds": 30,
            },
            {
                "action": "get_applicant_statuses",
                "method": "GET",
                "path_template": "/applicant_tracking/statuses",
                "headers": {
                    "Accept": "application/json",
                },
                "required_fields": [],
                "field_mapping": {},
                "success_status_codes": [
                    200,
                ],
                "timeout_seconds": 30,
            },
            {
                "action": "get_hiring_leads",
                "method": "GET",
                "path_template": "/applicant_tracking/hiring_leads",
                "headers": {
                    "Accept": "application/json",
                },
                "required_fields": [],
                "field_mapping": {},
                "success_status_codes": [
                    200,
                ],
                "timeout_seconds": 30,
            },
            {
                "action": "get_locations",
                "method": "GET",
                "path_template": "/applicant_tracking/locations",
                "headers": {
                    "Accept": "application/json",
                },
                "required_fields": [],
                "field_mapping": {},
                "success_status_codes": [
                    200,
                ],
                "timeout_seconds": 30,
            },
            {
                "action": "list_employees",
                "method": "GET",
                "path_template": "/employees",
                "headers": {
                    "Accept": "application/json",
                },
                "required_fields": [],
                "field_mapping": {},
                "success_status_codes": [
                    200,
                ],
                "timeout_seconds": 30,
            },
            {
                "action": "get_employee",
                "method": "GET",
                "path_template": "/employees/{{ employee_id }}",
                "headers": {
                    "Accept": "application/json",
                },
                "required_fields": [
                    "employee_id",
                ],
                "field_mapping": {},
                "success_status_codes": [
                    200,
                ],
                "timeout_seconds": 30,
            },
            {
                "action": "update_employee",
                "method": "POST",
                "path_template": "/employees/{{ employee_id }}",
                "headers": {
                    "Accept": "application/json",
                    "Content-Type": "application/json",
                },
                "required_fields": [
                    "employee_id",
                ],
                "field_mapping": {},
                "request_body_template": "{{ fields | tojson }}",
                "success_status_codes": [
                    200,
                ],
                "timeout_seconds": 30,
            },
            {
                "action": "get_company_information",
                "method": "GET",
                "path_template": "/company_information",
                "headers": {
                    "Accept": "application/json",
                },
                "required_fields": [],
                "field_mapping": {},
                "success_status_codes": [
                    200,
                ],
                "timeout_seconds": 30,
            },
            {
                "action": "get_employee_directory",
                "method": "GET",
                "path_template": "/employees/directory",
                "headers": {
                    "Accept": "application/json",
                },
                "required_fields": [],
                "field_mapping": {},
                "success_status_codes": [
                    200,
                ],
                "timeout_seconds": 30,
            },
            {
                "action": "create_file_category",
                "method": "POST",
                "path_template": "/files/categories",
                "headers": {
                    "Accept": "application/json",
                    "Content-Type": "application/json",
                },
                "required_fields": [
                    "categories",
                ],
                "field_mapping": {},
                "request_body_template": "{{ categories | tojson }}",
                "success_status_codes": [
                    200,
                ],
                "timeout_seconds": 30,
            },
            {
                "action": "get_company_files",
                "method": "GET",
                "path_template": "/files/view",
                "headers": {
                    "Accept": "application/json",
                },
                "required_fields": [],
                "field_mapping": {},
                "success_status_codes": [
                    200,
                ],
                "timeout_seconds": 30,
            },
            {
                "action": "get_company_file",
                "method": "GET",
                "path_template": "/files/{{ file_id }}",
                "headers": {},
                "required_fields": [
                    "file_id",
                ],
                "field_mapping": {},
                "success_status_codes": [
                    200,
                ],
                "timeout_seconds": 30,
            },
            {
                "action": "update_company_file",
                "method": "POST",
                "path_template": "/files/{{ file_id }}",
                "headers": {
                    "Accept": "application/json",
                    "Content-Type": "application/json",
                },
                "required_fields": [
                    "file_id",
                ],
                "field_mapping": {},
                "request_body_template": "{{ metadata | tojson }}",
                "success_status_codes": [
                    200,
                ],
                "timeout_seconds": 30,
            },
            {
                "action": "delete_company_file",
                "method": "DELETE",
                "path_template": "/files/{{ file_id }}",
                "headers": {},
                "required_fields": [
                    "file_id",
                ],
                "field_mapping": {},
                "success_status_codes": [
                    200,
                    204,
                ],
                "timeout_seconds": 30,
            },
            {
                "action": "create_employee_file_category",
                "method": "POST",
                "path_template": "/employees/files/categories",
                "headers": {
                    "Accept": "application/json",
                    "Content-Type": "application/json",
                },
                "required_fields": [
                    "categories",
                ],
                "field_mapping": {},
                "request_body_template": "{{ categories | tojson }}",
                "success_status_codes": [
                    200,
                ],
                "timeout_seconds": 30,
            },
            {
                "action": "get_employee_files",
                "method": "GET",
                "path_template": "/employees/{{ employee_id }}/files/view",
                "headers": {
                    "Accept": "application/json",
                },
                "required_fields": [
                    "employee_id",
                ],
                "field_mapping": {},
                "success_status_codes": [
                    200,
                ],
                "timeout_seconds": 30,
            },
            {
                "action": "get_employee_file",
                "method": "GET",
                "path_template": "/employees/{{ employee_id }}/files/{{ file_id }}",
                "headers": {},
                "required_fields": [
                    "employee_id",
                    "file_id",
                ],
                "field_mapping": {},
                "success_status_codes": [
                    200,
                ],
                "timeout_seconds": 30,
            },
            {
                "action": "update_employee_file",
                "method": "POST",
                "path_template": "/employees/{{ employee_id }}/files/{{ file_id }}",
                "headers": {
                    "Accept": "application/json",
                    "Content-Type": "application/json",
                },
                "required_fields": [
                    "employee_id",
                    "file_id",
                ],
                "field_mapping": {},
                "request_body_template": "{{ metadata | tojson }}",
                "success_status_codes": [
                    200,
                ],
                "timeout_seconds": 30,
            },
            {
                "action": "delete_employee_file",
                "method": "DELETE",
                "path_template": "/employees/{{ employee_id }}/files/{{ file_id }}",
                "headers": {},
                "required_fields": [
                    "employee_id",
                    "file_id",
                ],
                "field_mapping": {},
                "success_status_codes": [
                    200,
                    204,
                ],
                "timeout_seconds": 30,
            },
            {
                "action": "get_applicant_resume",
                "method": "GET",
                "description": "Fetch an applicant resume/CV file by its file ID (from resumeFileId in application details). Returns the document content so you can read and assess it.",
                "path_template": "/files/{{ file_id }}",
                "headers": {
                    "Accept": "application/json",
                },
                "required_fields": [
                    "file_id",
                ],
                "field_descriptions": {
                    "file_id": "The resumeFileId from the application details response",
                },
                "field_mapping": {},
                "success_status_codes": [
                    200,
                ],
                "timeout_seconds": 30,
            },
        ],
        "credential_fields": [
            {
                "key": "subdomain",
                "label": "Subdomain",
                "secret": False,
            },
            {
                "key": "api_key",
                "label": "API Key",
                "secret": True,
            },
        ],
        "oauth_config": None,
        "test_request": {
            "method": "GET",
            "path_template": "/applicant_tracking/jobs",
            "headers": {},
            "success_status_codes": [
                200,
            ],
            "timeout_seconds": 15,
        },
    },
    {
        "connector_name": "bidfood",
        "display_name": "Bidfood",
        "category": "procurement",
        "execution_mode": "template",
        "base_url_template": "https://api.bidfood.co.nz/v2",
        "auth_type": "api_key_header",
        "auth_config": {
            "header_name": "X-API-Key",
        },
        "tools": [
            {
                "action": "create_order",
                "description": "Submit a purchase order to Bidfood",
                "method": "POST",
                "path_template": "/orders",
                "headers": {
                    "Content-Type": "application/json",
                },
                "required_fields": [
                    "supplier",
                    "venue",
                    "lines",
                ],
                "field_mapping": {
                    "product_name": "productCode",
                    "venue_name": "deliveryLocation",
                    "quantity": "qty",
                },
                "request_body_template": '{"customerCode": "{{ creds.customer_code }}", "deliveryLocation": "{{ venue_name }}", "lines": [{"productCode": "{{ product_code }}", "description": "{{ product_name }}", "qty": {{ quantity }}, "unit": "{{ unit | default(\'case\') }}"}], "notes": "{{ notes | default_if_none }}"}',
                "success_status_codes": [
                    200,
                    201,
                ],
                "response_ref_path": "body.orderReference",
                "timeout_seconds": 30,
                "display_props": {
                    "title": "Purchase Order",
                    "connector_name": "bidfood",
                },
                "display_component": "purchase_order_editor",
                "working_document": {
                    "doc_type": "order",
                    "sync_mode": "submit",
                    "ref_fields": [
                        "venue_name",
                    ],
                },
                "field_descriptions": {
                    "supplier": "Supplier name (e.g., Bidfood)",
                    "venue": "Venue/delivery location name (e.g., La Zeppa)",
                    "lines": "Array of line items to order",
                },
                "field_schema": {
                    "lines": {
                        "type": "array",
                        "description": "Array of line items to order",
                        "items": {
                            "type": "object",
                            "properties": {
                                "stock_code": {
                                    "type": "string",
                                    "description": "Supplier stock/SKU code",
                                },
                                "product": {
                                    "type": "string",
                                    "description": "Product name",
                                },
                                "supplier": {
                                    "type": "string",
                                    "description": "Supplier for this item",
                                },
                                "quantity": {
                                    "type": "number",
                                    "description": "Order quantity",
                                },
                                "unit": {
                                    "type": "string",
                                    "description": "Unit of measure (e.g., case, each, kg)",
                                },
                                "unit_price": {
                                    "type": "number",
                                    "description": "Price per unit",
                                },
                            },
                            "required": [
                                "stock_code",
                                "product",
                                "supplier",
                                "quantity",
                            ],
                        },
                    },
                },
            },
            {
                "action": "check_stock",
                "description": "Check stock availability for a product",
                "method": "GET",
                "path_template": "/products/{{ product_code }}/availability",
                "headers": {},
                "required_fields": [
                    "product_name",
                ],
                "field_mapping": {
                    "product_name": "productCode",
                },
                "request_body_template": None,
                "success_status_codes": [
                    200,
                ],
                "response_ref_path": "body.available",
                "timeout_seconds": 15,
            },
        ],
        "credential_fields": [
            {
                "key": "api_key",
                "label": "API Key",
                "secret": True,
            },
            {
                "key": "customer_code",
                "label": "Customer Code",
                "secret": False,
            },
        ],
        "oauth_config": None,
        "test_request": None,
    },
    {
        "connector_name": "deputy",
        "display_name": "Deputy",
        "category": "hr",
        "execution_mode": "template",
        "base_url_template": "https://{{ creds.install_url }}/api/v1",
        "auth_type": "bearer",
        "auth_config": {},
        "tools": [
            {
                "action": "create_roster",
                "description": "Create a shift/roster entry for a staff member",
                "method": "POST",
                "path_template": "/resource/Roster",
                "headers": {
                    "Content-Type": "application/json",
                },
                "required_fields": [
                    "employee_name",
                    "venue_name",
                    "start_time",
                    "end_time",
                ],
                "field_mapping": {
                    "employee_name": "Employee",
                    "venue_name": "OperationalUnit",
                    "start_time": "StartTime",
                    "end_time": "EndTime",
                },
                "request_body_template": '{"Employee": "{{ employee_id }}", "OperationalUnit": "{{ venue_id }}", "StartTime": "{{ start_time }}", "EndTime": "{{ end_time }}", "Comment": "{{ notes | default_if_none }}"}',
                "success_status_codes": [
                    200,
                ],
                "response_ref_path": "body.Id",
                "timeout_seconds": 15,
            },
            {
                "action": "list_rosters",
                "description": "List upcoming roster entries for a venue",
                "method": "GET",
                "path_template": "/resource/Roster",
                "headers": {},
                "required_fields": [
                    "venue_name",
                ],
                "field_mapping": {
                    "venue_name": "OperationalUnit",
                },
                "request_body_template": None,
                "success_status_codes": [
                    200,
                ],
                "response_ref_path": None,
                "timeout_seconds": 15,
            },
        ],
        "credential_fields": [
            {
                "key": "install_url",
                "label": "Install URL (e.g. mycompany.na.deputy.com)",
                "secret": False,
            },
            {
                "key": "access_token",
                "label": "Permanent Access Token",
                "secret": True,
            },
        ],
        "oauth_config": None,
        "test_request": None,
    },
    {
        "connector_name": "loadedhub",
        "display_name": "LoadedHub",
        "category": "hr",
        "execution_mode": "template",
        "base_url_template": "https://",
        "auth_type": "bearer",
        "auth_config": {
            "Content-Type": "application/json",
            "x-loaded-company-id": "{{ creds.x_loaded_company_id }}",
        },
        "tools": [
            {
                "action": "get_user_session",
                "method": "GET",
                "path_template": "//loadedhub.com/api/user-session",
                "headers": {},
                "required_fields": [],
                "field_mapping": {},
                "field_descriptions": {},
                "success_status_codes": [
                    200,
                ],
                "timeout_seconds": 30,
            },
            {
                "action": "get_roster",
                "description": "Get a roster by date including all shifts",
                "method": "GET",
                "path_template": "//loadedhub.com/api/time/rosters?endTime={{ end_datetime }}&includeCrossVenueShifts=true&startTime={{ start_datetime }}",
                "headers": {
                    "Content-Type": "application/json",
                    "x-loaded-company-id": "{{ creds.x_loaded_company_id }}",
                },
                "required_fields": [
                    "end_datetime",
                    "start_datetime",
                ],
                "field_mapping": {
                    "end_datetime": "end_datetime",
                    "start_datetime": "start_datetime",
                },
                "request_body_template": None,
                "success_status_codes": [
                    200,
                ],
                "response_ref_path": None,
                "timeout_seconds": 60,
                "display_component": "roster_editor",
                "display_props": {
                    "title": "Roster",
                    "connector_name": "loadedhub",
                },
                "working_document": {
                    "doc_type": "roster",
                    "sync_mode": "auto",
                    "ref_fields": [
                        "search_date",
                    ],
                },
                "field_descriptions": {
                    "end_datetime": "Time in ISO 8601 format with timezone (e.g., 2025-03-18T21:32:55%2B13:00)",
                    "start_datetime": "Time in ISO 8601 format with timezone (e.g., 2025-03-18T21:32:55%2B13:00)",
                },
                "response_transform": {
                    "enabled": True,
                    "fields": {
                        "id": "id",
                        "templateName": "templateName",
                        "startDateTime": "startDateTime",
                        "endDateTime": "endDateTime",
                        "datestampDeleted": "datestampDeleted",
                        "datestampModified": "datestampModified",
                        "datestampPublished": "datestampPublished",
                        "datestampCreated": "datestampCreated",
                        "datestampLocked": "datestampLocked",
                        "totalHours": "totalHours",
                        "rosteredShifts[].rosterId": "",
                        "rosteredShifts[].staffMemberShowInRoster": "staffMemberShowInRoster",
                        "rosteredShifts[].staffMemberDatestampDeleted": "staffMemberDatestampDeleted",
                        "rosteredShifts[].hourlyRate": "hourlyRate",
                        "rosteredShifts[].adjustedHourlyRate": "adjustedHourlyRate",
                        "rosteredShifts[].jobs": "jobs",
                        "rosteredShifts[].clockoutTime": "clockoutTime",
                        "rosteredShifts[].datestampModified": "datestampModified",
                        "rosteredShifts[].rules": "rules",
                        "rosteredShifts[].id": "id",
                        "rosteredShifts[].type": "type",
                        "rosteredShifts[].remunerationType": "remunerationType",
                        "rosteredShifts[].posIdentifier": "posIdentifier",
                        "rosteredShifts[].staffMemberId": "staffMemberId",
                        "rosteredShifts[].staffMemberFirstName": "staffMemberFirstName",
                        "rosteredShifts[].staffMemberLastName": "staffMemberLastName",
                        "rosteredShifts[].staffMemberPayCode": "",
                        "rosteredShifts[].roleId": "",
                        "rosteredShifts[].roleName": "roleName",
                        "rosteredShifts[].adjustedHourlyRatePrecise": "adjustedHourlyRatePrecise",
                        "rosteredShifts[].clockinTime": "clockinTime",
                        "rosteredShifts[].datestampCreated": "datestampCreated",
                        "rosteredShifts[].datestampDeleted": "datestampDeleted",
                        "rosteredShifts[].isFinalised": "isFinalised",
                        "rosteredShifts[].breaks": "breaks",
                        "rosteredShifts[].payRateBreakdown[].title": "title",
                        "rosteredShifts[].payRateBreakdown[].hoursWorked": "hoursWorked",
                        "rosteredShifts[].payRateBreakdown[].payRate": "payRate",
                        "rosteredShifts[].payRateBreakdown[].total": "total",
                        "rosteredShifts[].payRateBreakdown[].periodStart": "periodStart",
                        "rosteredShifts[].payRateBreakdown[].periodEnd": "periodEnd",
                        "rosteredShifts[].payRateBreakdown[].isOrdinaryPay": "isOrdinaryPay",
                        "rosteredShifts[].payRateBreakdown[].includeInPayrollExport": "",
                        "rosteredShifts[].payRateBreakdown[].order": "order",
                        "rosteredShifts[].showOnFinancialReports": "",
                        "rosteredShifts[].venueId": "",
                        "rosteredShifts[].venueFullName": "",
                        "rosteredShifts[].venueShortName": "",
                        "rosteredShifts[].isFromOtherCompany": "isFromOtherCompany",
                    },
                },
            },
            {
                "action": "update_shift",
                "method": "PUT",
                "path_template": "//loadedhub.com/api/time/rostered-shifts/{{ shift_id }}",
                "headers": {
                    "Content-Type": "application/json",
                    "x-loaded-company-id": "{{ creds.x_loaded_company_id }}",
                },
                "required_fields": [
                    "shift_id",
                    "roster_id",
                    "staff_member_id",
                    "role_id",
                    "venue_id",
                    "clockin_time",
                    "clockout_time",
                    "hourly_rate",
                ],
                "field_mapping": {
                    "shift_id": "shift_id",
                    "roster_id": "roster_id",
                    "staff_member_id": "staff_member_id",
                    "role_id": "role_id",
                    "venue_id": "venue_id",
                    "clockin_time": "clockin_time",
                    "clockout_time": "clockout_time",
                    "hourly_rate": "hourly_rate",
                },
                "field_descriptions": {
                    "shift_id": "Unique identifier for the shift to update (e.g., 97078f06-2c9f-449d-8e31-08de7f9d8865)",
                    "roster_id": "Unique identifier for the roster (e.g., b57dcfd4-e3a6-4091-3491-08de7fa2996b)",
                    "staff_member_id": "Unique identifier for the staff member (e.g., 92c04ae5-ec80-4308-9226-435f67161609)",
                    "role_id": "Unique identifier for the role (e.g., 580ec2cc-ee6e-4a26-a29e-b3ebfabf44ea)",
                    "venue_id": "Unique identifier for the venue (e.g., 9e850c10-8d5e-4619-bbff-51c170398486)",
                    "clockin_time": "Time in ISO 8601 format with timezone (e.g., 2025-03-18T21:32:55%2B13:00)",
                    "clockout_time": "Time in ISO 8601 format with timezone (e.g., 2025-03-18T21:32:55%2B13:00)",
                    "hourly_rate": "Hourly pay rate as a decimal number (e.g., 46.5)",
                },
                "request_body_template": '{"rosterId":"{{ roster_id }}","staffMemberShowInRoster":true,"staffMemberDatestampDeleted":null,"hourlyRate":{{ hourly_rate }},"adjustedHourlyRate":{{ hourly_rate }},"jobs":null,"clockoutTime":"{{ clockout_time }}","datestampModified":null,"rules":[],"type":"Roster","remunerationType":"HourlyRate","posIdentifier":null,"staffMemberId":"{{ staff_member_id }}","roleId":"{{ role_id }}","adjustedHourlyRatePrecise":{{ hourly_rate }},"clockinTime":"{{ clockin_time }}","isFinalised":false,"breaks":[],"showOnFinancialReports":true,"venueId":"{{ venue_id }}","isFromOtherCompany":false,"datestampLocked":null,"datestampPublished":null,"saving":true}',
                "success_status_codes": [
                    200,
                    204,
                ],
                "response_ref_path": "id",
                "timeout_seconds": 30,
                "description": "Update a rostered shift",
            },
            {
                "action": "create_rostered_shift",
                "method": "POST",
                "path_template": "//loadedhub.com/api/time/rostered-shifts",
                "headers": {
                    "Content-Type": "application/json",
                    "x-loaded-company-id": "{{ creds.x_loaded_company_id }}",
                },
                "required_fields": [
                    "role_id",
                    "role_name",
                    "staff_member_id",
                    "clockin_time",
                    "clockout_time",
                ],
                "field_mapping": {
                    "role_id": "roleId",
                    "role_name": "roleName",
                    "staff_member_id": "staffMemberId",
                    "clockin_time": "clockinTime",
                    "clockout_time": "clockoutTime",
                },
                "field_descriptions": {
                    "role_id": "Unique identifier for the role in UUID format (e.g., 580ec2cc-ee6e-4a26-a29e-b3ebfabf44ea)",
                    "role_name": "Name of the role being assigned (e.g., Sales Manager)",
                    "staff_member_id": "Unique identifier for the staff member in UUID format (e.g., 92c04ae5-ec80-4308-9226-435f67161609)",
                    "clockin_time": "Time in ISO 8601 format with timezone (e.g., 2025-03-18T21:32:55%2B13:00)",
                    "clockout_time": "Time in ISO 8601 format with timezone (e.g., 2025-03-18T21:32:55%2B13:00)",
                },
                "request_body_template": '{"roleId": "{{ role_id }}", "roleName": "{{ role_name }}", "staffMemberId": "{{ staff_member_id }}", "clockinTime": "{{ clockin_time }}", "clockoutTime": "{{ clockout_time }}", "breaks": [], "saving": true, "type": "Roster"}',
                "success_status_codes": [
                    200,
                    201,
                ],
                "response_ref_path": "id",
                "timeout_seconds": 30,
                "description": "Create a rostered shift for an employee",
            },
            {
                "action": "delete_rostered_shift",
                "method": "DELETE",
                "path_template": "//loadedhub.com/api/time/rostered-shifts/{{ shift_id }}",
                "headers": {
                    "Content-Type": "application/json",
                    "x-loaded-company-id": "{{ creds.x_loaded_company_id }}",
                },
                "required_fields": [
                    "shift_id",
                ],
                "field_mapping": {
                    "shift_id": "shift_id",
                },
                "field_descriptions": {
                    "shift_id": "UUID of the rostered shift to delete (e.g., 97078f06-2c9f-449d-8e31-08de7f9d8865)",
                },
                "request_body_template": "",
                "success_status_codes": [
                    200,
                    204,
                ],
                "response_ref_path": "id",
                "timeout_seconds": 30,
                "description": "Delete a rostered shift",
            },
            {
                "action": "get_sales_data",
                "method": "GET",
                "path_template": "//loadedhub.com/api//pos/sales?end={{ end_datetime }}&interval={{ interval | default('1.00:00:00') }}&start={{ start_datetime }}",
                "headers": {
                    "Content-Type": "application/json",
                    "x-loaded-company-id": "{{ creds.x_loaded_company_id }}",
                },
                "required_fields": [
                    "start_datetime",
                    "end_datetime",
                    "interval",
                ],
                "field_mapping": {
                    "start_datetime": "start_datetime",
                    "end_datetime": "end_datetime",
                    "interval": "interval",
                },
                "request_body_template": None,
                "success_status_codes": [
                    200,
                ],
                "response_ref_path": None,
                "timeout_seconds": 30,
                "description": "Get total sales for a period broken down by a certain time interval",
                "field_descriptions": {
                    "start_datetime": "Time in ISO 8601 format with timezone (e.g., 2025-03-18T21:32:55%2B13:00)",
                    "end_datetime": "Time in ISO 8601 format with timezone (e.g., 2025-03-18T21:32:55%2B13:00)",
                    "interval": "Timespan (e.g. 1.00:00:00)",
                },
                "response_transform": {
                    "enabled": True,
                    "fields": {
                        "startTime": "startTime",
                        "period": "",
                        "invoices": "invoices",
                        "invoicesTax": "invoicesTax",
                        "discounts": "discounts",
                        "surcharges": "surcharges",
                        "quantity": "quantity",
                        "count": "count",
                    },
                },
            },
            {
                "action": "get_pos_orders",
                "method": "GET",
                "path_template": "//loadedhub.com/api//pos/orders?end={{ end }}&interval={{ interval }}&start={{ start }}",
                "headers": {
                    "Content-Type": "application/json",
                    "x-loaded-company-id": "{{ creds.x_loaded_company_id }}",
                },
                "required_fields": [
                    "start",
                    "end",
                    "interval",
                ],
                "field_mapping": {
                    "start": "start",
                    "end": "end",
                    "interval": "interval",
                },
                "request_body_template": "",
                "success_status_codes": [
                    200,
                ],
                "response_ref_path": "",
                "timeout_seconds": 30,
                "description": "Get total order for a period broken down by a certain time interval",
                "field_descriptions": {
                    "start": "Time in ISO 8601 format with timezone (e.g., 2025-03-18T21:32:55%2B13:00)",
                    "end": "Time in ISO 8601 format with timezone (e.g., 2025-03-18T21:32:55%2B13:00)",
                    "interval": "Sales breakdown interval in HH:MM:SS (e.g. 00:30:00)",
                },
                "response_transform": {
                    "enabled": True,
                    "fields": {
                        "startTime": "startTime",
                        "period": "",
                        "amount": "amount",
                        "count": "count",
                    },
                },
            },
            {
                "action": "get_pos_item_sales",
                "method": "GET",
                "path_template": "//loadedhub.com/api//pos/items/sales?startTime={{ start_time }}&endTime={{ end_time }}",
                "headers": {
                    "Content-Type": "application/json",
                    "x-loaded-company-id": "{{ creds.x_loaded_company_id }}",
                },
                "required_fields": [
                    "start_time",
                    "end_time",
                ],
                "field_mapping": {
                    "start_time": "startTime",
                    "end_time": "endTime",
                },
                "field_descriptions": {
                    "start_time": "Time in ISO 8601 format with timezone (e.g., 2025-03-18T21:32:55%2B13:00)",
                    "end_time": "Time in ISO 8601 format with timezone (e.g., 2025-03-18T21:32:55%2B13:00)",
                },
                "success_status_codes": [
                    200,
                ],
                "timeout_seconds": 30,
                "description": "Get total product sales for a period along with the group for each product (eg. food or beverage) and category (eg. beers, wines, spirits)",
                "response_transform": {
                    "enabled": True,
                    "fields": {
                        "itemName": "itemName",
                        "itemPosIdentifier": "",
                        "itemGroupName": "itemGroupName",
                        "itemGroupIdentifier": "",
                        "itemCategoryName": "itemCategoryName",
                        "itemCategoryIdentifier": "",
                        "invoices": "invoices",
                        "quantity": "quantity",
                    },
                },
            },
            {
                "action": "get_staff_orders",
                "method": "GET",
                "path_template": "//loadedhub.com/api//pos/staff/orders?start={{ start }}&end={{ end }}",
                "headers": {
                    "Content-Type": "application/json",
                    "x-loaded-company-id": "{{ creds.x_loaded_company_id }}",
                },
                "required_fields": [
                    "start",
                    "end",
                ],
                "field_mapping": {
                    "start": "start",
                    "end": "end",
                },
                "field_descriptions": {
                    "start": "Time in ISO 8601 format with timezone (e.g., 2025-03-18T21:32:55%2B13:00)",
                    "end": "Time in ISO 8601 format with timezone (e.g., 2025-03-18T21:32:55%2B13:00)",
                },
                "request_body_template": "",
                "success_status_codes": [
                    200,
                ],
                "response_ref_path": None,
                "timeout_seconds": 30,
                "description": "Get total orders for each staff member for a time period",
                "response_transform": {
                    "enabled": True,
                    "fields": {
                        "label": "label",
                        "id": "id",
                        "quantity": "quantity",
                        "amount": "amount",
                    },
                },
            },
            {
                "action": "get_staff_item_orders",
                "description": "Get product orders for a specific staff member over a specified time period",
                "method": "GET",
                "path_template": "//loadedhub.com/api//pos/staff/item-orders?end={{ end }}&posIdentifier={{ posIdentifier }}&start={{ start }}",
                "headers": {
                    "Content-Type": "application/json",
                    "x-loaded-company-id": "{{ creds.x_loaded_company_id }}",
                },
                "required_fields": [
                    "end",
                    "posIdentifier",
                    "start",
                ],
                "field_mapping": {
                    "end": "end",
                    "posIdentifier": "posIdentifier",
                    "start": "start",
                },
                "field_descriptions": {
                    "end": "Time in ISO 8601 format with timezone (e.g., 2025-03-18T21:32:55%2B13:00)",
                    "posIdentifier": "POS system identifier UUID (e.g., e0abec69-8d73-4f43-a29a-0d9dbf3f806e) this can be obtained by calling get_staff_orders for the same time period",
                    "start": "Time in ISO 8601 format with timezone (e.g., 2025-03-18T21:32:55%2B13:00)",
                },
                "request_body_template": "",
                "success_status_codes": [
                    200,
                ],
                "response_ref_path": "data",
                "timeout_seconds": 30,
            },
            {
                "action": "get_pos_discounts",
                "method": "GET",
                "path_template": "//loadedhub.com/api//pos/discounts?start={{ start }}&end={{ end }}",
                "headers": {
                    "Content-Type": "application/json",
                    "x-loaded-company-id": "{{ creds.x_loaded_company_id }}",
                },
                "required_fields": [
                    "start",
                    "end",
                ],
                "field_mapping": {
                    "start": "start",
                    "end": "end",
                },
                "field_descriptions": {
                    "start": "Time in ISO 8601 format with timezone (e.g., 2025-03-18T21:32:55%2B13:00)",
                    "end": "Time in ISO 8601 format with timezone (e.g., 2025-03-18T21:32:55%2B13:00)",
                },
                "request_body_template": None,
                "success_status_codes": [
                    200,
                ],
                "response_ref_path": None,
                "timeout_seconds": 30,
                "description": "Get total discount by staff member over a specific time period",
            },
            {
                "action": "get_stock_items",
                "description": "Get all available stock items with supplier information, pricing, and inventory details",
                "method": "GET",
                "path_template": "//loadedhub.com/api/StockItems",
                "headers": {
                    "Content-Type": "application/json",
                    "x-loaded-company-id": "{{ creds.x_loaded_company_id }}",
                },
                "required_fields": [],
                "field_mapping": {},
                "field_descriptions": {},
                "request_body_template": "",
                "success_status_codes": [
                    200,
                ],
                "response_ref_path": "",
                "timeout_seconds": 30,
                "summary_fields": [
                    "name",
                    "id",
                    "groupName",
                ],
                "response_transform": {
                    "enabled": True,
                    "fields": {
                        "id": "id",
                        "groupId": "",
                        "groupName": "",
                        "name": "name",
                        "countingUnitId": "",
                        "countingUnitName": "",
                        "countingUnitRatio": "",
                        "orderingUnitId": "",
                        "orderingUnitName": "",
                        "orderingUnitRatio": "",
                        "currentPrice": "",
                        "globalPrice": "",
                        "globalSalesTaxRate": "",
                        "currentPricePurchaseOrderOrderNumber": "",
                        "currentPricePurchaseOrderId": "",
                        "datestampRemoved": "",
                        "defaultSupplierId": "",
                        "defaultSupplierName": "",
                        "defaultSupplierValidateStockCode": "",
                        "defaultBrandId": "",
                        "defaultBrandName": "",
                        "masterId": "",
                        "isShared": "",
                    },
                    "filters": [
                        {
                            "field": "datestampRemoved",
                            "operator": "is_empty",
                            "value": "",
                        },
                    ],
                },
            },
            {
                "action": "get_stock_item",
                "description": "Get all details for a specific stock item including minimum stock on hand and variants. Use this when ordering to get the default ordering variant and the stock code for that variant",
                "method": "GET",
                "path_template": "//api.loadedhub.com/1.0/stock/internal/items/{{ item_id }}?includeDeleted={{ include_deleted }}",
                "headers": {
                    "Content-Type": "application/json",
                    "x-loaded-company-id": "{{ creds.x_loaded_company_id }}",
                },
                "required_fields": [
                    "item_id",
                    "include_deleted",
                ],
                "field_mapping": {
                    "item_id": "item_id",
                    "include_deleted": "include_deleted",
                },
                "field_descriptions": {
                    "item_id": "UUID identifier of the stock item (e.g., 0ab8b774-2deb-4828-8c3e-00298a5f7041)",
                    "include_deleted": "Whether to include deleted items, true or false (e.g., true)",
                },
                "success_status_codes": [
                    200,
                ],
                "timeout_seconds": 30,
            },
            {
                "action": "get_suppliers",
                "description": "Get all stock suppliers with optional inclusion of deleted suppliers",
                "method": "GET",
                "path_template": "//api.loadedhub.com/1.0/stock/internal/suppliers{% if include_deleted %}?includeDeletedSuppliers=true{% endif %}",
                "headers": {
                    "Content-Type": "application/json",
                    "x-loaded-company-id": "{{ creds.x_loaded_company_id }}",
                },
                "required_fields": [],
                "field_mapping": {
                    "include_deleted": "include_deleted",
                },
                "field_descriptions": {
                    "include_deleted": "Boolean flag to include deleted suppliers in the response (e.g., true)",
                },
                "success_status_codes": [
                    200,
                ],
                "timeout_seconds": 30,
            },
            {
                "action": "get_stock_units",
                "description": "Get all stock units including name, ratio and unit type",
                "method": "GET",
                "path_template": "//loadedhub.com/wapi/r/StockUnits?$filter=MasterId+eq+null",
                "headers": {
                    "Content-Type": "application/json",
                    "x-loaded-company-id": "{{ creds.x_loaded_company_id }}",
                },
                "required_fields": [],
                "field_mapping": {},
                "field_descriptions": {},
                "success_status_codes": [
                    200,
                ],
                "timeout_seconds": 30,
            },
            {
                "action": "get_recipe_details",
                "description": "Get detailed recipe information including all ingredients, quantities, units and recipe versions",
                "method": "GET",
                "path_template": "//api.loadedhub.com/1.0/stock/internal/recipes/{{ recipe_id }}",
                "headers": {
                    "Content-Type": "application/json",
                    "x-loaded-company-id": "{{ creds.x_loaded_company_id }}",
                },
                "required_fields": [
                    "recipe_id",
                ],
                "field_mapping": {
                    "recipe_id": "recipe_id",
                },
                "field_descriptions": {
                    "recipe_id": "UUID identifier for the recipe (e.g., 747ed69a-73f0-4f6c-9c07-671184c36296)",
                },
                "success_status_codes": [
                    200,
                ],
                "timeout_seconds": 30,
            },
            {
                "action": "get_all_recipes",
                "description": "Get all recipes with detailed information including ingredients, quantities, and cooking instructions",
                "method": "GET",
                "path_template": "//api.loadedhub.com/1.0//stock/internal/recipes",
                "headers": {
                    "Content-Type": "application/json",
                    "x-loaded-company-id": "{{ creds.x_loaded_company_id }}",
                },
                "required_fields": [],
                "field_mapping": {},
                "field_descriptions": {},
                "success_status_codes": [
                    200,
                ],
                "timeout_seconds": 30,
            },
            {
                "action": "get_outstanding_invoices",
                "description": "Get a list of all outstanding invoices that have not been received",
                "method": "GET",
                "path_template": "//api.loadedhub.com/1.0/stock/internal/invoices?status={{ status }}&page={{ page }}&pageSize={{ pageSize }}&sort={{ sort }}",
                "headers": {
                    "Content-Type": "application/json",
                    "x-loaded-company-id": "{{ creds.x_loaded_company_id }}",
                },
                "required_fields": [
                    "status",
                    "page",
                    "pageSize",
                    "sort",
                ],
                "field_mapping": {
                    "status": "status",
                    "page": "page",
                    "pageSize": "pageSize",
                    "sort": "sort",
                },
                "field_descriptions": {
                    "status": "Invoice status filter (e.g., NotReceived)",
                    "page": "Page number for pagination, starting from 0 (e.g., 0)",
                    "pageSize": "Number of results per page (e.g., 20)",
                    "sort": "Sort order field and direction (e.g., issuedAt desc)",
                },
                "success_status_codes": [
                    200,
                ],
                "timeout_seconds": 30,
            },
            {
                "action": "get_stock_on_hand",
                "description": "Get a quantity and value of stock on hand for a particular date. When using this report to find the stock on hand for an item first decided if it is a food, beverage or other item and then call the corresponding stocktake template",
                "method": "GET",
                "path_template": "//loadedhub.com/api/stock/stock-on-hand?templateId={{ template_id }}&reportDateTime={{ report_datetime }}",
                "headers": {
                    "Content-Type": "application/json",
                    "x-loaded-company-id": "{{ creds.x_loaded_company_id }}",
                },
                "required_fields": [
                    "template_id",
                    "report_datetime",
                ],
                "field_mapping": {
                    "template_id": "templateId",
                    "report_datetime": "reportDateTime",
                },
                "field_descriptions": {
                    "template_id": "Stocktake template ID - use get_stocktake_templates to get this id (e.g., a134765a-241f-4cbc-a319-0a6c28a4b387)",
                    "report_datetime": "Report date and time in ISO 8601 format with timezone (e.g., 2025-03-18T21:32:55%2B13:00)",
                },
                "success_status_codes": [
                    200,
                ],
                "timeout_seconds": 30,
                "summary_fields": [
                    "itemName",
                    "stocktakeQuantity",
                    "header",
                ],
                "response_transform": {
                    "enabled": True,
                    "fields": {
                        "header": "Category",
                        "itemId": "stockItemID",
                        "itemName": "itemName",
                        "isItemDeleted": "isItemDeleted",
                        "countingUnitId": "",
                        "countingUnitName": "countingUnitName",
                        "countingUnitRatio": "countingUnitRatio",
                        "stocktakeQuantity": "",
                        "stocktakeUnitId": "",
                        "stocktakeUnitName": "",
                        "stocktakeUnitRatio": "",
                        "stocktakeId": "",
                        "stocktakeTitle": "",
                        "stocktakeCompletedAt": "",
                        "receivedQuantity": "",
                        "stockReceivedLines": "",
                        "usedQuantity": "",
                        "stockUsages[].posItemName": "",
                        "stockUsages[].posItemPosIdentifier": "",
                        "stockUsages[].posOrdered": "",
                        "stockUsages[].posWasted": "",
                        "stockUsages[].startTime": "",
                        "stockUsages[].endTime": "",
                        "stockUsages[].posItemLinkQuantity": "",
                        "stockUsages[].posItemLinkPortion": "",
                        "stockUsages[].posItemLinkUnitRatio": "",
                        "lastPrice.id": "",
                        "lastPrice.at": "",
                        "lastPrice.cost": "",
                        "lastPrice.salesTaxRate": "",
                        "lastPrice.documentId": "",
                        "lastPrice.documentType": "",
                        "lastPrice.documentNumber": "",
                        "lastPrice.documentReceivedAt": "",
                        "lastPrice.unitId": "",
                        "lastPrice.unitName": "",
                        "lastPrice.unitRatio": "",
                        "sortOrder": "",
                        "calculatedUsage": "",
                        "quantityOnHand": "quantityOnHand",
                        "valueOnHand": "valueOnHand",
                        "minimumStockOnHandQuantity": "",
                        "minimumStockOnHandUnitId": "",
                    },
                },
            },
            {
                "action": "get_stocktake_templates",
                "description": "Get a list of available stocktake templates that are not deleted",
                "method": "GET",
                "path_template": "//api.loadedhub.com/1.0/stock/internal/stocktake-templates?includeDeleted=false",
                "headers": {
                    "Content-Type": "application/json",
                    "x-loaded-company-id": "{{ creds.x_loaded_company_id }}",
                },
                "required_fields": [],
                "field_mapping": {},
                "field_descriptions": {},
                "success_status_codes": [
                    200,
                ],
                "timeout_seconds": 30,
            },
            {
                "action": "get_completed_stocktakes",
                "description": "Get all completed stocktakes for a specific time period",
                "method": "GET",
                "path_template": "//loadedhub.com/wapi/r/Stocktakes?$filter=Pending+eq+false+and+DatestampCompleted+gt+{{ start_date }}+and+DatestampCompleted+lt+{{ end_date }}&$orderby=DateStampCreated+desc",
                "headers": {
                    "Content-Type": "application/json",
                    "x-loaded-company-id": "{{ creds.x_loaded_company_id }}",
                },
                "required_fields": [
                    "start_date",
                    "end_date",
                ],
                "field_mapping": {
                    "start_date": "start_date",
                    "end_date": "end_date",
                },
                "field_descriptions": {
                    "start_date": "Start date in ISO 8601 format with timezone (e.g., 2025-03-18T21:32:55%2B13:00)",
                    "end_date": "End date in ISO 8601 format with timezone (e.g., 2025-03-18T21:32:55%2B13:00)",
                },
                "success_status_codes": [
                    200,
                ],
                "timeout_seconds": 30,
            },
            {
                "action": "generate_stocktake_report",
                "description": "Generate a stocktake report between two stocktakes including opening, closing, received, calculated usage and unit cost for each item",
                "method": "GET",
                "path_template": "//loadedhub.com/api/stock/stocktakereport/generate?endStocktake={{ end_stocktake_id }}&startStocktake={{ start_stocktake_id }}",
                "headers": {
                    "Content-Type": "application/json",
                    "x-loaded-company-id": "{{ creds.x_loaded_company_id }}",
                },
                "required_fields": [
                    "end_stocktake_id",
                    "start_stocktake_id",
                ],
                "field_mapping": {
                    "end_stocktake_id": "end_stocktake_id",
                    "start_stocktake_id": "start_stocktake_id",
                },
                "field_descriptions": {
                    "end_stocktake_id": "UUID of the ending stocktake (e.g., 8b0a2dcb-1f67-4612-9978-1aa0a4481fc1)",
                    "start_stocktake_id": "UUID of the starting stocktake (e.g., 2b1eb30c-31d7-4aab-ab8d-2e3a40d1c69a)",
                },
                "success_status_codes": [
                    200,
                ],
                "timeout_seconds": 30,
            },
            {
                "action": "get_received_invoices",
                "description": "Get received invoices or credits for a date range. Returns full details including stock variant codes and quantities but not product names. Use norm__get_received_stock to get received stock quantities",
                "method": "GET",
                "path_template": "//api.loadedhub.com/1.0/stock/internal/stock-received?from={{ from }}&to={{ to }}&property=Received",
                "headers": {
                    "Content-Type": "application/json",
                    "x-loaded-company-id": "{{ creds.x_loaded_company_id }}",
                },
                "required_fields": [
                    "from",
                    "to",
                ],
                "field_mapping": {
                    "from": "from",
                    "to": "to",
                },
                "field_descriptions": {
                    "from": "Start date and time in ISO 8601 format with timezone (e.g., 2025-03-18T21:32:55%2B13:00)",
                    "to": "End date and time in ISO 8601 format with timezone (e.g., 2025-03-18T21:32:55%2B13:00)",
                },
                "request_body_template": "",
                "success_status_codes": [
                    200,
                ],
                "response_ref_path": "",
                "timeout_seconds": 30,
                "summary_fields": [
                    "type",
                    "purchaseOrderNumber",
                    "receivedAt",
                ],
                "response_transform": {
                    "enabled": True,
                    "fields": {
                        "id": "id",
                        "type": "type",
                        "creditRequest": "creditRequest",
                        "purchaseOrderNumber": "",
                        "invoiceNumber": "invoiceNumber",
                        "fileId": "",
                        "receivedAt": "receivedAt",
                        "orderedAt": "",
                        "createdBy": "",
                        "invoicedAt": "invoicedAt",
                        "lines[].id": "",
                        "lines[].itemId": "StockItemId",
                        "lines[].itemCode": "StockVariantCode",
                        "lines[].itemCategory.id": "",
                        "lines[].itemCategory.name": "Category",
                        "lines[].itemCategory.subcategories": "",
                        "lines[].itemCategory.externalAccountCode": "",
                        "lines[].unitId": "",
                        "lines[].unitName": "unitName",
                        "lines[].unitRatio": "unitRatio",
                        "lines[].brandName": "",
                        "lines[].brandId": "",
                        "lines[].quantityReceived": "quantityReceived",
                        "lines[].quantityOrdered": "",
                        "lines[].unitCost": "unitCost",
                        "lines[].saleTaxRate": "saleTaxRate",
                        "freight": "freight",
                        "subtotal": "subtotal",
                        "total": "total",
                        "orderedBy": "orderedBy",
                        "supplierId": "supplierId",
                        "supplierName": "supplierName",
                        "statementId": "",
                        "reconciled": "",
                        "thirdPartyInvoiceId": "",
                    },
                    "flatten": [],
                },
            },
            {
                "action": "get_budgets",
                "description": "Get daily budget data for a specified date range",
                "method": "GET",
                "path_template": "//loadedhub.com/api/budgets?from={{ from_date }}&to={{ to_date }}",
                "headers": {
                    "Content-Type": "application/json",
                    "x-loaded-company-id": "{{ creds.x_loaded_company_id }}",
                },
                "required_fields": [
                    "from_date",
                    "to_date",
                ],
                "field_mapping": {
                    "from_date": "from",
                    "to_date": "to",
                },
                "field_descriptions": {
                    "from_date": "Start date in YYYY-MM-DD format (e.g., 2026-02-23)",
                    "to_date": "End date in YYYY-MM-DD format (e.g., 2026-04-06)",
                },
                "request_body_template": "",
                "success_status_codes": [
                    200,
                ],
                "response_ref_path": "",
                "timeout_seconds": 30,
                "response_transform": {
                    "enabled": True,
                    "fields": {
                        "id": "",
                        "date": "date",
                        "amount": "amount",
                        "salesTax": "salesTax",
                    },
                },
            },
            {
                "action": "get_stock_item_groups",
                "description": "Get all stock item groups and their associated supergroups",
                "method": "GET",
                "path_template": "//loadedhub.com/wapi/r/StockItemGroups",
                "headers": {
                    "Content-Type": "application/json",
                    "x-loaded-company-id": "{{ creds.x_loaded_company_id }}",
                },
                "required_fields": [],
                "field_mapping": {},
                "field_descriptions": {},
                "request_body_template": "",
                "success_status_codes": [
                    200,
                ],
                "response_ref_path": "",
                "timeout_seconds": 30,
                "response_transform": {
                    "enabled": True,
                    "fields": {
                        "id": "id",
                        "superGroupId": "",
                        "superGroupName": "superGroupName",
                        "name": "name",
                        "masterId": "",
                    },
                },
            },
            {
                "action": "get_timeclock_entries",
                "description": "Get timeclock entries for a specified time period with filtering options",
                "method": "GET",
                "path_template": "//loadedhub.com/api/time-clockins?startTime={{ start_time }}&endTime={{ end_time }}&includeInactive={{ include_inactive }}&includeOnlyClockins={{ include_only_clockins }}&shouldTruncateShifts={{ should_truncate_shifts }}",
                "headers": {
                    "Content-Type": "application/json",
                    "x-loaded-company-id": "{{ creds.x_loaded_company_id }}",
                },
                "required_fields": [
                    "start_time",
                    "end_time",
                    "include_inactive",
                    "include_only_clockins",
                    "should_truncate_shifts",
                ],
                "field_mapping": {
                    "start_time": "startTime",
                    "end_time": "endTime",
                    "include_inactive": "includeInactive",
                    "include_only_clockins": "includeOnlyClockins",
                    "should_truncate_shifts": "shouldTruncateShifts",
                },
                "field_descriptions": {
                    "start_time": "Start time in ISO 8601 format with timezone (e.g., 2026-03-15T18:00:00.000Z)",
                    "end_time": "End time in ISO 8601 format with timezone (e.g., 2026-03-22T18:00:00.000Z)",
                    "include_inactive": "Whether to include inactive entries - true or false (e.g., false)",
                    "include_only_clockins": "Whether to include only clock-in entries - true or false (e.g., false)",
                    "should_truncate_shifts": "Whether to truncate shifts - true or false (e.g., true)",
                },
                "request_body_template": None,
                "success_status_codes": [
                    200,
                ],
                "response_ref_path": None,
                "timeout_seconds": 30,
                "response_transform": {
                    "enabled": True,
                    "fields": {
                        "type": "type",
                        "datestampApproved": "datestampApproved",
                        "rules": "",
                        "isCompanyClockin": "",
                        "id": "",
                        "remunerationType": "remunerationType",
                        "posIdentifier": "",
                        "staffMemberId": "staffMemberId",
                        "staffMemberFirstName": "staffMemberFirstName",
                        "staffMemberLastName": "staffMemberLastName",
                        "staffMemberPayCode": "",
                        "roleId": "",
                        "roleName": "roleName",
                        "hourlyRate": "hourlyRate",
                        "adjustedHourlyRate": "adjustedHourlyRate",
                        "adjustedHourlyRatePrecise": "adjustedHourlyRatePrecise",
                        "clockinTime": "clockinTime",
                        "clockoutTime": "clockoutTime",
                        "datestampCreated": "datestampCreated",
                        "datestampDeleted": "datestampDeleted",
                        "isFinalised": "",
                        "breaks[].id": "id",
                        "breaks[].type": "type",
                        "breaks[].shiftId": "shiftId",
                        "breaks[].breakStart": "breakStart",
                        "breaks[].breakEnd": "breakEnd",
                        "breaks[].paid": "paid",
                        "breaks[].createdAt": "createdAt",
                        "breaks[].deletedAt": "deletedAt",
                        "payRateBreakdown[].title": "",
                        "payRateBreakdown[].hoursWorked": "",
                        "payRateBreakdown[].payRate": "",
                        "payRateBreakdown[].total": "",
                        "payRateBreakdown[].periodStart": "",
                        "payRateBreakdown[].periodEnd": "",
                        "payRateBreakdown[].isOrdinaryPay": "",
                        "payRateBreakdown[].includeInPayrollExport": "",
                        "payRateBreakdown[].order": "",
                        "showOnFinancialReports": "",
                        "venueId": "",
                        "venueFullName": "venueFullName",
                        "venueShortName": "",
                        "isFromOtherCompany": "isFromOtherCompany",
                    },
                },
            },
            {
                "action": "get_staff_members",
                "description": "Get a list of staff members with optional inclusion of deleted members and last clock times",
                "method": "GET",
                "path_template": "//loadedhub.com/api/staff-members?includeDeleted={{ include_deleted }}&includeLastClocks={{ include_last_clocks }}",
                "headers": {
                    "Content-Type": "application/json",
                    "x-loaded-company-id": "{{ creds.x_loaded_company_id }}",
                },
                "required_fields": [
                    "include_deleted",
                    "include_last_clocks",
                ],
                "field_mapping": {
                    "include_deleted": "includeDeleted",
                    "include_last_clocks": "includeLastClocks",
                },
                "field_descriptions": {
                    "include_deleted": "Whether to include deleted staff members - true or false (e.g., true)",
                    "include_last_clocks": "Whether to include last clock times - true or false (e.g., false)",
                },
                "request_body_template": None,
                "success_status_codes": [
                    200,
                ],
                "response_ref_path": None,
                "timeout_seconds": 30,
                "response_transform": {
                    "enabled": True,
                    "fields": {
                        "id": "id",
                        "datestampDeleted": "",
                        "name": "name",
                        "email": "email",
                        "phoneMobile": "phoneMobile",
                        "posStaffName": "",
                        "defaultMemberRoleRoleId": "defaultMemberRoleRoleId",
                        "defaultMemberRoleRoleName": "defaultMemberRoleRoleName",
                        "defaultMemberRoleHourlyRate": "defaultMemberRoleHourlyRate",
                        "salaryRate": "salaryRate",
                        "memberRoles[].id": "",
                        "memberRoles[].remunerationTimelineVenueId": "",
                        "memberRoles[].roleId": "roleId",
                        "memberRoles[].roleName": "roleName",
                        "memberRoles[].hourlyRate": "hourlyRate",
                        "memberRoles[].code": "",
                        "memberRoles[].payClassificationId": "",
                        "memberRoles[].payClassificationName": "",
                        "memberRoles[].payClassificationVariationId": "",
                        "lastClockId": "",
                        "lastClockRoleName": "",
                        "lastClockClockinTime": "",
                        "lastClockClockoutTime": "",
                        "lastClockBreaks": "",
                        "showInRoster": "",
                        "remunerationType": "remunerationType",
                        "payCode": "payCode",
                        "isExported": "",
                        "activeRemuneration.id": "",
                        "activeRemuneration.staffMemberId": "",
                        "activeRemuneration.createdAt": "",
                        "activeRemuneration.deletedAt": "",
                        "activeRemuneration.validFrom": "",
                        "activeRemuneration.validTo": "",
                        "activeRemuneration.type": "",
                        "activeRemuneration.salaryRate": "salaryRate",
                        "activeRemuneration.employmentType": "",
                        "activeRemuneration.weeklyContractedHours": "",
                        "activeRemuneration.payrollVenueId": "payrollVenueId",
                        "activeRemuneration.venueAssignments[].id": "id",
                        "activeRemuneration.venueAssignments[].remunerationTimelineId": "",
                        "activeRemuneration.venueAssignments[].venueId": "venueId",
                        "activeRemuneration.venueAssignments[].venueFullName": "venueFullName",
                        "activeRemuneration.venueAssignments[].venueShortName": "",
                        "activeRemuneration.venueAssignments[].defaultRoleId": "defaultRoleId",
                        "activeRemuneration.venueAssignments[].defaultRoleName": "defaultRoleName",
                        "activeRemuneration.venueAssignments[].memberRoles[].id": "id",
                        "activeRemuneration.venueAssignments[].memberRoles[].remunerationTimelineVenueId": "remunerationTimelineVenueId",
                        "activeRemuneration.venueAssignments[].memberRoles[].roleId": "roleId",
                        "activeRemuneration.venueAssignments[].memberRoles[].roleName": "roleName",
                        "activeRemuneration.venueAssignments[].memberRoles[].hourlyRate": "hourlyRate",
                        "activeRemuneration.venueAssignments[].memberRoles[].code": "code",
                        "activeRemuneration.venueAssignments[].memberRoles[].payClassificationId": "payClassificationId",
                        "activeRemuneration.venueAssignments[].memberRoles[].payClassificationName": "payClassificationName",
                        "activeRemuneration.venueAssignments[].memberRoles[].payClassificationVariationId": "payClassificationVariationId",
                        "activeRemuneration.venueAssignments[].assignedAt": "assignedAt",
                        "activeRemuneration.venueAssignments[].unassignedAt": "",
                        "activeRemuneration.venueAssignments[].isActive": "isActive",
                        "activeRemuneration.defaultRoleId": "defaultRoleId",
                        "activeRemuneration.memberRoles[].id": "id",
                        "activeRemuneration.memberRoles[].remunerationTimelineVenueId": "remunerationTimelineVenueId",
                        "activeRemuneration.memberRoles[].roleId": "roleId",
                        "activeRemuneration.memberRoles[].roleName": "roleName",
                        "activeRemuneration.memberRoles[].hourlyRate": "hourlyRate",
                        "activeRemuneration.memberRoles[].code": "code",
                        "activeRemuneration.memberRoles[].payClassificationId": "payClassificationId",
                        "activeRemuneration.memberRoles[].payClassificationName": "payClassificationName",
                        "activeRemuneration.memberRoles[].payClassificationVariationId": "payClassificationVariationId",
                        "activeRemuneration.isActive": "",
                        "nextRemuneration": "",
                        "previousRemuneration": "",
                    },
                    "filters": [
                        {
                            "field": "datestampDeleted",
                            "operator": "is_empty",
                            "value": "",
                        },
                    ],
                },
            },
        ],
        "credential_fields": [
            {
                "key": "x_loaded_company_id",
                "label": "Company ID (x-loaded-company-id header)",
                "secret": False,
            },
            {
                "key": "api_key",
                "label": "API Token",
                "secret": True,
            },
        ],
        "oauth_config": {
            "authorize_url": "https://test.loadedhub.com/api/oauth/authorize",
            "token_url": "https://test.loadedhub.com/api/token",
            "scopes": "core:time:rw",
            "client_id": "testing",
            "client_secret": "FTn9A0Zfwj1J31GymJ7YONsloym5zAa+YIGJa0BuCUo=",
        },
        "test_request": {
            "method": "GET",
            "path_template": "/user-session",
            "headers": {},
            "success_status_codes": [
                200,
            ],
            "timeout_seconds": 15,
        },
    },
    # ── Email connectors ──────────────────────────────────────────────
    {
        "connector_name": "gmail",
        "display_name": "Gmail (Send on Behalf)",
        "category": "email",
        "execution_mode": "internal",
        "auth_type": "oauth2",
        "oauth_config": {
            "authorize_url": "https://accounts.google.com/o/oauth2/v2/auth",
            "token_url": "https://oauth2.googleapis.com/token",
            "scopes": "https://www.googleapis.com/auth/gmail.send https://www.googleapis.com/auth/userinfo.email",
            "client_id": "_FROM_SETTINGS:GOOGLE_CLIENT_ID",
            "client_secret": "_FROM_SETTINGS:GOOGLE_CLIENT_SECRET",
        },
        "tools": [
            {
                "action": "send_email",
                "method": "POST",
                "description": "Send an email from the user's Gmail account.",
                "required_fields": ["to", "subject", "body_html"],
                "optional_fields": ["cc", "bcc"],
                "field_descriptions": {
                    "to": "Recipient email address(es), comma-separated",
                    "subject": "Email subject line",
                    "body_html": "HTML body of the email",
                    "cc": "CC email address(es), comma-separated",
                    "bcc": "BCC email address(es), comma-separated",
                },
            },
        ],
    },
    {
        "connector_name": "microsoft_outlook",
        "display_name": "Outlook (Send on Behalf)",
        "category": "email",
        "execution_mode": "internal",
        "auth_type": "oauth2",
        "oauth_config": {
            "authorize_url": "https://login.microsoftonline.com/common/oauth2/v2.0/authorize",
            "token_url": "https://login.microsoftonline.com/common/oauth2/v2.0/token",
            "scopes": "https://graph.microsoft.com/Mail.Send offline_access",
            "client_id": "",
            "client_secret": "",
        },
        "tools": [
            {
                "action": "send_email",
                "method": "POST",
                "description": "Send an email from the user's Outlook account.",
                "required_fields": ["to", "subject", "body_html"],
                "optional_fields": ["cc", "bcc"],
                "field_descriptions": {
                    "to": "Recipient email address(es), comma-separated",
                    "subject": "Email subject line",
                    "body_html": "HTML body of the email",
                    "cc": "CC email address(es), comma-separated",
                    "bcc": "BCC email address(es), comma-separated",
                },
            },
        ],
    },
    {
        "connector_name": "norm_email",
        "display_name": "Norm System Email",
        "category": "email",
        "execution_mode": "internal",
        "auth_type": "none",
        "tools": [
            {
                "action": "send_notification",
                "method": "POST",
                "description": "Send a system notification email from noreply@norm.com.",
                "required_fields": ["to", "template_name", "template_context"],
                "field_descriptions": {
                    "to": "Recipient email address(es), comma-separated",
                    "template_name": "Template name (e.g., task_complete, billing_receipt)",
                    "template_context": "JSON object with template variables",
                },
                "field_schema": {
                    "template_context": {
                        "type": "object",
                        "description": "Key-value pairs for template rendering",
                    },
                },
            },
        ],
    },
]


# ---------------------------------------------------------------------------
# Agent Configs — define the known agent slugs and their default metadata
# ---------------------------------------------------------------------------

AGENT_CONFIGS: list[dict] = [
    {
        "agent_slug": "router",
        "display_name": "Router",
        "description": "Classifies messages and routes to the right agent",
        "system_prompt": (
            """\
You are a message router for Norm, a hospitality operations platform.
Given a user message and the list of available domains, classify which domain
should handle this message, and generate a short title for the task.

Available domains:
- procurement: Predictions for stock usage, Orders stock from suppliers for venues (can: Submit a purchase order to Bidfood, Get total sales for a period broken down by a certain time interval, Get all available stock items with supplier information, pricing, and inventory details, Get all details for a specific stock item including minimum stock on hand and variants. Use this when ordering to get the default ordering variant and the stock code for that variant, Get all stock suppliers with optional inclusion of deleted suppliers, Get all stock units including name, ratio and unit type, Get detailed recipe information including all ingredients, quantities, units and recipe versions, Get all recipes with detailed information including ingredients, quantities, and cooking instructions, Get a list of all outstanding invoices that have not been received, Get a list of stock on hand for a particular date including how the stock on hand value was calculated. When using this report to find the stock on hand for an item first decided if it is a food, beverage or other item and then call the corresponding stocktake template, Get a list of available stocktake templates that are not deleted, Get all completed stocktakes for a specific time period, Generate a stocktake report between two stocktakes including opening, closing, received, calculated usage and unit cost for each item, Get all received stock items including credits over a specified time period, Get daily budget data for a specified date range)
- hr: View and set hiring criteria, sets up new employees at venues (can: Get User Session, Get a roster by date including all shifts, Update a rostered shift, Create a rostered shift for an employee, Delete a rostered shift, Get Jobs, Get Applications, Get Application Details, Get Applicant Statuses, List Employees, Get Employee Files, Get Employee File)
- reports: Generates sales and inventory reports (can: Get User Session, Get total sales for a period broken down by a certain time interval, Get total order for a period broken down by a certain time interval, Get total product sales for a period along with the group for each product (eg. food or beverage) and category (eg. beers, wines, spirits), Get total orders for each staff member for a time period, Get product orders for a specific staff member over a specified time period, Get total discount by staff member over a specific time period, Get daily budget data for a specified date range)
- meta: ONLY when the user asks a general question about the whole system's capabilities without mentioning a specific domain (e.g. 'what can you do?', 'help'). If they mention a specific area like HR, procurement, or reports, route to that domain instead.

Title guidelines:
- 3-6 words, no articles (a/an/the)
- Describe the user's goal, not the domain (e.g. "Weekly roster request" not "HR query")
- Use sentence case (capitalize first word only)
- No trailing punctuation

Return ONLY valid JSON:
{"domain": "<domain-slug or unknown>", "confidence": 0.0-1.0, "title": "<short task title>"}
"""
        ),
    },
    {
        "agent_slug": "procurement",
        "display_name": "Procurement Agent",
        "description": "Orders stock from suppliers for venues",
        "system_prompt": (
            """\
You are the procurement agent for Norm, a hospitality operations platform.
You help users by using the available tools to query data and perform actions.

## Date awareness
Today's date is {{today}}. Use this date to resolve relative time references like "today", "this week", "yesterday", "last month", etc. When calling tools that require date parameters, calculate the correct dates from this prefix.

## Rules
- Use tools to gather information needed to answer the user's question.
- You may call multiple tools in sequence to build a complete picture.
- For queries that span multiple periods (e.g. "last 4 Sundays", "each day last week"), make separate tool calls for each period, then combine and summarise the results.
- For read-only tools (GET), proceed immediately — they execute automatically.
- For write tools (POST/PUT/DELETE), describe what you plan to do and call the tool — the user will be asked to approve before it executes.
- Always explain what you found or did in clear, natural language.
- If you need more information from the user, ask a clear question.
- Match entity names fuzzily: "zeppa" = "La Zeppa", "jb" = "Jim Beam".
- Be concise and helpful.
- IMPORTANT: The example value in the description shows the correct format — follow it precisely. IMPORTANT: When calling tools that require dates, use the EXACT format shown in that tool's field description example. Different tools may require different formats — always  follow the specific example for each tool.
- IMPORTANT: When you are about to call a tool, you MUST start your text response with the prefix "[Tool]" followed by a brief explanation of what you are looking up or doing and why. Example: "[Tool] Looking up staff orders for last week to find Arthur's sales data." Do NOT use the [Tool] prefix when giving your final answer to the user.
- When making a stock order you must find the default stock variant from loadedhub and use that for the order. Use get_item with the correct id to get this after you have used get_items
- CRITICAL: You must ONLY present data that was returned by tool calls. NEVER fabricate, invent, extrapolate, or estimate data beyond what the tools returned. If a tool
 returns partial or no data, report exactly what was returned — do not fill in gaps. If you cannot answer the question from the tool results alone, say so clearly and ask for more information. You can explain what information you do have access to

## Tips
- To calculate a how much stock is required over a period of time follow this process exactly. You should make the calls for the two stock on hand reports, received stock and budget in parallel so you have all information to make the calculation. Only follow this process and thoroughly read the api responses as they may be complex. Do not use other reports.
  STEP 1: Calculated usage over the last 4 weeks
  a: Run two get_stock_on_hand_by_date - one for today and one for 4 weeks ago
  c: Run get_received_stock for the same time period
  d: From this work out the usage over the 4 weeks - opening count + stock received - closing count = used
  STEP 2: Calculate daily usage
  a: Get the total sales over the last 4 weeks
  b: From the total sales for the last 4 weeks calculate what the usage is per $1,000 spend
  STEP 3: Calculate usage based on budget
  a: Get the budget for the period from now until the date the user has requested to order stock until
  b: Based on the budget and the usage per $1,000 calculate how much is required

## Formatting
- If presenting tabular data, use markdown tables.
- Keep summaries brief — highlight counts, key facts, and anything unusual.
- When summarising data across multiple periods, present a comparison table showing each period side by side.
- For confirmations, use **bold** labels: **Reference**: ORD-12345."""
        ),
    },
    {
        "agent_slug": "hr",
        "display_name": "HR Agent",
        "description": "Sets up new employees at venues",
        "system_prompt": (
            """\
You are the hr agent for Norm, a hospitality operations platform.
You help users by using the available tools to query data and perform actions.

## Date awareness
Today's date is {{today}}. Use this date to resolve relative time references like "today", "this week", "yesterday", "last month", etc. When calling tools that require date parameters, calculate the correct dates from this prefix.

## Rules
- Use tools to gather information needed to answer the user's question.
- You may call multiple tools in sequence to build a complete picture.
- For queries that span multiple periods (e.g. "last 4 Sundays", "each day last week"), make separate tool calls for each period, then combine and summarise the results.
- For read-only tools (GET), proceed immediately — they execute automatically.
- For write tools (POST/PUT/DELETE), describe what you plan to do and call the tool — the user will be asked to approve before it executes.
- Always explain what you found or did in clear, natural language.
- If you need more information from the user, ask a clear question.
- Match entity names fuzzily: "zeppa" = "La Zeppa", "jb" = "Jim Beam".
- If there is only ONE venue in the context, use it automatically — do NOT ask the user to choose.
- Prefer action over clarification. For read operations, make reasonable assumptions and proceed. Only ask for clarification when essential info is truly missing for a write operation.
- Be concise and helpful.
- IMPORTANT: When calling tools, use the EXACT format specified in the field description. If it says "ISO 8601 format with timezone (e.g., 2026-03-23T07:00:00+13:00)", use that exact format including the timezone offset. Do NOT use other date formats. The example value in the description shows the correct format — follow it precisely.
- IMPORTANT: When you are about to call a tool, you MUST start your text response with the prefix "[Tool] " followed by a brief explanation of what you are looking up or doing and why. Example: "[Tool] Looking up staff orders for last week to find Arthur's sales data." Do NOT use the [Tool] prefix when giving your final answer to the user.
- CRITICAL: You must ONLY present data that was returned by tool calls. NEVER fabricate, invent, extrapolate, or estimate data beyond what the tools returned. If a tool
 returns partial or no data, report exactly what was returned — do not fill in gaps. If you cannot answer the question from the tool results alone, say so clearly and ask for more information. You can explain what information you do have access to

## Formatting
- If presenting tabular data, use markdown tables.
- Keep summaries brief — highlight counts, key facts, and anything unusual.
- When summarising data across multiple periods, present a comparison table showing each period side by side.
- For confirmations, use **bold** labels: **Reference**: ORD-12345."""
        ),
    },
    {
        "agent_slug": "reports",
        "display_name": "Reports Agent",
        "description": "Generates sales and inventory reports",
        "system_prompt": (
            """\
You are the reports agent for Norm, a hospitality operations platform.
You help users by using the available tools to query data and perform actions.

## Date awareness
Today's date is {{today}}. Use this date to resolve relative time references like "today", "this week", "yesterday", "last month", etc. When calling tools that require date parameters, calculate the correct dates from this prefix.

## Rules
- Do not search data for more than one month. If the requested period is longer run multiple queries
- Use tools to gather information needed to answer the user's question.
- You may call multiple tools in sequence to build a complete picture.
- For queries that span multiple periods (e.g. "last 4 Sundays", "each day last week"), make separate tool calls for each period, then combine and summarise the results.
- For read-only tools (GET), proceed immediately — they execute automatically.
- For write tools (POST/PUT/DELETE), describe what you plan to do and call the tool — the user will be asked to approve before it executes.
- Always explain what you found or did in clear, natural language.
- If you need more information from the user, ask a clear question.
- Match entity names fuzzily: "zeppa" = "La Zeppa", "jb" = "Jim Beam".
- If there is only ONE venue in the context, use it automatically — do NOT ask the user to choose.
- Prefer action over clarification. For read operations, make reasonable assumptions and proceed. Only ask for clarification when essential info is truly missing for a write operation.
- Be concise and helpful.
- IMPORTANT: When calling tools, use the EXACT format specified in the field description. If it says "ISO 8601 format with timezone (e.g., 2026-03-23T07:00:00+13:00)", use that exact format including the timezone offset. Do NOT use other date formats. The example value in the description shows the correct format — follow it precisely.
- IMPORTANT: When you are about to call a tool, you MUST start your text response with the prefix "[Tool] " followed by a brief explanation of what you are looking up or doing and why. Example: "[Tool] Looking up staff orders for last week to find Arthur's sales data." Do NOT use the [Tool] prefix when giving your final answer to the user.
- CRITICAL: You must ONLY present data that was returned by tool calls. NEVER fabricate, invent, extrapolate, or estimate data beyond what the tools returned. If a tool
 returns partial or no data, report exactly what was returned — do not fill in gaps. If you cannot answer the question from the tool results alone, say so clearly and ask for more information. You can explain what information you do have access to
- A week period is 7:00am Monday to 6:59am Monday unless otherwise stated. When sending date formats use timezone UTC+13

## Formatting
- If presenting tabular data, use markdown tables.
- When presenting data from a tool call, use the `render_chart` tool to create a visual chart.
- Keep summaries brief — highlight counts, key facts, and anything unusual.
- When summarising data across multiple periods, present a comparison table showing each period side by side.
- For confirmations, use **bold** labels: **Reference**: ORD-12345."""
        ),
    },
]


# ---------------------------------------------------------------------------
# Agent ↔ Connector Bindings — wire agents to their connector specs
#
# capabilities list defines which tools from the spec are enabled by default.
# Set enabled=True for tools that should be on for new deployments.
# ---------------------------------------------------------------------------

AGENT_BINDINGS: list[dict] = [
    # Every agent gets the norm search tool
    {
        "agent_slug": "router",
        "connector_name": "norm",
        "capabilities": [
            {
                "action": "search_tool_result",
                "label": "Search through a previous tool call's full result by keyword.",
                "enabled": True,
            },
        ],
    },
    {
        "agent_slug": "procurement",
        "connector_name": "norm",
        "capabilities": [
            {
                "action": "search_tool_result",
                "label": "Search through a previous tool call's full result by keyword.",
                "enabled": True,
            },
        ],
    },
    {
        "agent_slug": "hr",
        "connector_name": "norm",
        "capabilities": [
            {
                "action": "search_tool_result",
                "label": "Search through a previous tool call's full result by keyword.",
                "enabled": True,
            },
        ],
    },
    {
        "agent_slug": "reports",
        "connector_name": "norm",
        "capabilities": [
            {
                "action": "search_tool_result",
                "label": "Search through a previous tool call's full result by keyword.",
                "enabled": True,
            },
        ],
    },
    # Reports agent gets the charting tool
    {
        "agent_slug": "reports",
        "connector_name": "norm_reports",
        "capabilities": [
            {
                "action": "render_chart",
                "label": "Render data as a visual chart",
                "enabled": True,
            },
        ],
    },
    # --- External bindings ---
    {
        "agent_slug": "hr",
        "connector_name": "bamboohr",
        "capabilities": [
            {
                "action": "create_employee",
                "label": "Create a new employee record in BambooHR",
                "enabled": False,
            },
            {
                "action": "terminate_employee",
                "label": "Terminate an existing employee in BambooHR",
                "enabled": False,
            },
            {
                "action": "get_jobs",
                "label": "Get Jobs",
                "enabled": True,
            },
            {
                "action": "get_applications",
                "label": "Get Applications",
                "enabled": True,
            },
            {
                "action": "get_application_details",
                "label": "Get Application Details",
                "enabled": True,
            },
            {
                "action": "get_applicant_statuses",
                "label": "Get Applicant Statuses",
                "enabled": True,
            },
            {
                "action": "get_hiring_leads",
                "label": "Get Hiring Leads",
                "enabled": False,
            },
            {
                "action": "get_locations",
                "label": "Get Locations",
                "enabled": False,
            },
            {
                "action": "list_employees",
                "label": "List Employees",
                "enabled": True,
            },
            {
                "action": "get_employee",
                "label": "Get Employee",
                "enabled": False,
            },
            {
                "action": "update_employee",
                "label": "Update Employee",
                "enabled": False,
            },
            {
                "action": "get_company_information",
                "label": "Get Company Information",
                "enabled": False,
            },
            {
                "action": "get_employee_directory",
                "label": "Get Employee Directory",
                "enabled": False,
            },
            {
                "action": "create_file_category",
                "label": "Create File Category",
                "enabled": False,
            },
            {
                "action": "get_company_files",
                "label": "Get Company Files",
                "enabled": False,
            },
            {
                "action": "get_company_file",
                "label": "Get Company File",
                "enabled": False,
            },
            {
                "action": "update_company_file",
                "label": "Update Company File",
                "enabled": False,
            },
            {
                "action": "delete_company_file",
                "label": "Delete Company File",
                "enabled": False,
            },
            {
                "action": "create_employee_file_category",
                "label": "Create Employee File Category",
                "enabled": False,
            },
            {
                "action": "get_employee_files",
                "label": "Get Employee Files",
                "enabled": False,
            },
            {
                "action": "get_employee_file",
                "label": "Get Employee File",
                "enabled": False,
            },
            {
                "action": "update_employee_file",
                "label": "Update Employee File",
                "enabled": False,
            },
            {
                "action": "delete_employee_file",
                "label": "Delete Employee File",
                "enabled": False,
            },
            {
                "action": "get_applicant_resume",
                "label": "Get Applicant Resume",
                "enabled": True,
            },
        ],
        "enabled": True,
    },
    {
        "agent_slug": "hr",
        "connector_name": "deputy",
        "capabilities": [
            {
                "action": "create_roster",
                "label": "Create shift/roster",
                "enabled": False,
            },
            {
                "action": "list_rosters",
                "label": "View upcoming rosters",
                "enabled": False,
            },
        ],
        "enabled": True,
    },
    {
        "agent_slug": "hr",
        "connector_name": "loadedhub",
        "capabilities": [
            {
                "action": "get_user_session",
                "label": "Get User Session",
                "enabled": True,
            },
            {
                "action": "get_roster",
                "label": "Get a roster by date including all shifts",
                "enabled": True,
            },
            {
                "action": "update_shift",
                "label": "Update a rostered shift",
                "enabled": True,
            },
            {
                "action": "create_rostered_shift",
                "label": "Create a rostered shift for an employee",
                "enabled": True,
            },
            {
                "action": "delete_rostered_shift",
                "label": "Delete a rostered shift",
                "enabled": True,
            },
            {
                "action": "get_sales_data",
                "label": "Get total sales for a period broken down by a certain time interval",
                "enabled": False,
            },
            {
                "action": "get_pos_orders",
                "label": "Get total order for a period broken down by a certain time interval",
                "enabled": False,
            },
            {
                "action": "get_pos_item_sales",
                "label": "Get total product sales for a period along with the group for each product (eg. food or beverage) and category (eg. beers, wines, spirits)",
                "enabled": False,
            },
            {
                "action": "get_staff_orders",
                "label": "Get total orders for each staff member for a time period",
                "enabled": False,
            },
            {
                "action": "get_staff_item_orders",
                "label": "Get product orders for a specific staff member over a specified time period",
                "enabled": False,
            },
            {
                "action": "get_pos_discounts",
                "label": "Get total discount by staff member over a specific time period",
                "enabled": False,
            },
            {
                "action": "get_stock_items",
                "label": "Get all available stock items with supplier information, pricing, and inventory details",
                "enabled": False,
            },
            {
                "action": "get_stock_item",
                "label": "Get all details for a specific stock item including minimum stock on hand and variants. Use this when ordering to get the default ordering variant and the stock code for that variant",
                "enabled": False,
            },
            {
                "action": "get_suppliers",
                "label": "Get all stock suppliers with optional inclusion of deleted suppliers",
                "enabled": False,
            },
            {
                "action": "get_stock_units",
                "label": "Get all stock units including name, ratio and unit type",
                "enabled": False,
            },
            {
                "action": "get_recipe_details",
                "label": "Get detailed recipe information including all ingredients, quantities, units and recipe versions",
                "enabled": False,
            },
            {
                "action": "get_all_recipes",
                "label": "Get all recipes with detailed information including ingredients, quantities, and cooking instructions",
                "enabled": False,
            },
            {
                "action": "get_outstanding_invoices",
                "label": "Get a list of all outstanding invoices that have not been received",
                "enabled": False,
            },
            {
                "action": "get_stock_on_hand",
                "label": "Get a quantity and value of stock on hand for a particular date. When using this report to find the stock on hand for an item first decided if it is a food, beverage or other item and then call the corresponding stocktake template",
                "enabled": False,
            },
            {
                "action": "get_stocktake_templates",
                "label": "Get a list of available stocktake templates that are not deleted",
                "enabled": False,
            },
            {
                "action": "get_completed_stocktakes",
                "label": "Get all completed stocktakes for a specific time period",
                "enabled": False,
            },
            {
                "action": "generate_stocktake_report",
                "label": "Generate a stocktake report between two stocktakes including opening, closing, received, calculated usage and unit cost for each item",
                "enabled": False,
            },
            {
                "action": "get_received_invoices",
                "label": "Get received invoices or credits for a date range. Returns full details including stock variant codes and quantities but not product names. Use norm__get_received_stock to get received stock quantities",
                "enabled": False,
            },
            {
                "action": "get_budgets",
                "label": "Get daily budget data for a specified date range",
                "enabled": False,
            },
            {
                "action": "get_stock_item_groups",
                "label": "Get all stock item groups and their associated supergroups",
                "enabled": False,
            },
            {
                "action": "get_timeclock_entries",
                "label": "Get timeclock entries for a specified time period with filtering options",
                "enabled": True,
            },
            {
                "action": "get_staff_members",
                "label": "Get a list of staff members with optional inclusion of deleted members and last clock times",
                "enabled": True,
            },
        ],
        "enabled": True,
    },
    {
        "agent_slug": "procurement",
        "connector_name": "bidfood",
        "capabilities": [
            {
                "action": "create_order",
                "label": "Submit a purchase order to Bidfood",
                "enabled": True,
            },
            {
                "action": "check_stock",
                "label": "Check stock availability for a product",
                "enabled": False,
            },
        ],
        "enabled": True,
    },
    {
        "agent_slug": "procurement",
        "connector_name": "loadedhub",
        "capabilities": [
            {
                "action": "get_user_session",
                "label": "Get User Session",
                "enabled": False,
            },
            {
                "action": "get_roster",
                "label": "Get a roster by date including all shifts",
                "enabled": False,
            },
            {
                "action": "update_shift",
                "label": "Update a rostered shift",
                "enabled": False,
            },
            {
                "action": "create_rostered_shift",
                "label": "Create a rostered shift for an employee",
                "enabled": False,
            },
            {
                "action": "delete_rostered_shift",
                "label": "Delete a rostered shift",
                "enabled": False,
            },
            {
                "action": "get_sales_data",
                "label": "Get total sales for a period broken down by a certain time interval",
                "enabled": True,
            },
            {
                "action": "get_pos_orders",
                "label": "Get total order for a period broken down by a certain time interval",
                "enabled": False,
            },
            {
                "action": "get_pos_item_sales",
                "label": "Get total product sales for a period along with the group for each product (eg. food or beverage) and category (eg. beers, wines, spirits)",
                "enabled": False,
            },
            {
                "action": "get_staff_orders",
                "label": "Get total orders for each staff member for a time period",
                "enabled": False,
            },
            {
                "action": "get_staff_item_orders",
                "label": "Get product orders for a specific staff member over a specified time period",
                "enabled": False,
            },
            {
                "action": "get_pos_discounts",
                "label": "Get total discount by staff member over a specific time period",
                "enabled": False,
            },
            {
                "action": "get_stock_items",
                "label": "Get all available stock items with supplier information, pricing, and inventory details",
                "enabled": True,
            },
            {
                "action": "get_stock_item",
                "label": "Get all details for a specific stock item including minimum stock on hand and variants. Use this when ordering to get the default ordering variant and the stock code for that variant",
                "enabled": True,
            },
            {
                "action": "get_suppliers",
                "label": "Get all stock suppliers with optional inclusion of deleted suppliers",
                "enabled": True,
            },
            {
                "action": "get_stock_units",
                "label": "Get all stock units including name, ratio and unit type",
                "enabled": True,
            },
            {
                "action": "get_recipe_details",
                "label": "Get detailed recipe information including all ingredients, quantities, units and recipe versions",
                "enabled": True,
            },
            {
                "action": "get_all_recipes",
                "label": "Get all recipes with detailed information including ingredients, quantities, and cooking instructions",
                "enabled": True,
            },
            {
                "action": "get_outstanding_invoices",
                "label": "Get a list of all outstanding invoices that have not been received",
                "enabled": True,
            },
            {
                "action": "get_stock_on_hand",
                "label": "Get a quantity and value of stock on hand for a particular date. When using this report to find the stock on hand for an item first decided if it is a food, beverage or other item and then call the corresponding stocktake template",
                "enabled": False,
            },
            {
                "action": "get_stocktake_templates",
                "label": "Get a list of available stocktake templates that are not deleted",
                "enabled": True,
            },
            {
                "action": "get_completed_stocktakes",
                "label": "Get all completed stocktakes for a specific time period",
                "enabled": True,
            },
            {
                "action": "generate_stocktake_report",
                "label": "Generate a stocktake report between two stocktakes including opening, closing, received, calculated usage and unit cost for each item",
                "enabled": True,
            },
            {
                "action": "get_received_stock",
                "label": "Get received stock/deliveries for a date range. Returns stock variant codes and quantities but NOT product names. To identify product names, first call   get_stock_items to look up stock item details, then cross-reference the stock codes with this data.",
                "enabled": True,
            },
            {
                "action": "get_budgets",
                "label": "Get daily budget data for a specified date range",
                "enabled": True,
            },
            {
                "action": "get_stock_item_groups",
                "label": "Get all stock item groups and their associated supergroups",
                "enabled": True,
            },
        ],
        "enabled": True,
    },
    {
        "agent_slug": "reports",
        "connector_name": "loadedhub",
        "capabilities": [
            {
                "action": "get_user_session",
                "label": "Get User Session",
                "enabled": True,
            },
            {
                "action": "get_roster",
                "label": "Get a roster by date including all shifts",
                "enabled": False,
            },
            {
                "action": "update_shift",
                "label": "Update a rostered shift",
                "enabled": False,
            },
            {
                "action": "create_rostered_shift",
                "label": "Create a rostered shift for an employee",
                "enabled": False,
            },
            {
                "action": "delete_rostered_shift",
                "label": "Delete a rostered shift",
                "enabled": False,
            },
            {
                "action": "get_sales_data",
                "label": "Get total sales for a period broken down by a certain time interval",
                "enabled": True,
            },
            {
                "action": "get_pos_orders",
                "label": "Get total order for a period broken down by a certain time interval",
                "enabled": True,
            },
            {
                "action": "get_pos_item_sales",
                "label": "Get total product sales for a period along with the group for each product (eg. food or beverage) and category (eg. beers, wines, spirits)",
                "enabled": True,
            },
            {
                "action": "get_staff_orders",
                "label": "Get total orders for each staff member for a time period",
                "enabled": True,
            },
            {
                "action": "get_staff_item_orders",
                "label": "Get product orders for a specific staff member over a specified time period",
                "enabled": True,
            },
            {
                "action": "get_pos_discounts",
                "label": "Get total discount by staff member over a specific time period",
                "enabled": True,
            },
            {
                "action": "get_stock_items",
                "label": "Get all available stock items with supplier information, pricing, and inventory details",
                "enabled": False,
            },
            {
                "action": "get_stock_item",
                "label": "Get all details for a specific stock item including minimum stock on hand and variants. Use this when ordering to get the default ordering variant and the stock code for that variant",
                "enabled": False,
            },
            {
                "action": "get_suppliers",
                "label": "Get all stock suppliers with optional inclusion of deleted suppliers",
                "enabled": False,
            },
            {
                "action": "get_stock_units",
                "label": "Get all stock units including name, ratio and unit type",
                "enabled": False,
            },
            {
                "action": "get_recipe_details",
                "label": "Get detailed recipe information including all ingredients, quantities, units and recipe versions",
                "enabled": False,
            },
            {
                "action": "get_all_recipes",
                "label": "Get all recipes with detailed information including ingredients, quantities, and cooking instructions",
                "enabled": False,
            },
            {
                "action": "get_outstanding_invoices",
                "label": "Get a list of all outstanding invoices that have not been received",
                "enabled": False,
            },
            {
                "action": "get_stock_on_hand",
                "label": "Get a quantity and value of stock on hand for a particular date. When using this report to find the stock on hand for an item first decided if it is a food, beverage or other item and then call the corresponding stocktake template",
                "enabled": False,
            },
            {
                "action": "get_stocktake_templates",
                "label": "Get a list of available stocktake templates that are not deleted",
                "enabled": False,
            },
            {
                "action": "get_completed_stocktakes",
                "label": "Get all completed stocktakes for a specific time period",
                "enabled": False,
            },
            {
                "action": "generate_stocktake_report",
                "label": "Generate a stocktake report between two stocktakes including opening, closing, received, calculated usage and unit cost for each item",
                "enabled": False,
            },
            {
                "action": "get_received_invoices",
                "label": "Get received invoices or credits for a date range. Returns full details including stock variant codes and quantities but not product names. Use norm__get_received_stock to get received stock quantities",
                "enabled": False,
            },
            {
                "action": "get_budgets",
                "label": "Get daily budget data for a specified date range",
                "enabled": True,
            },
            {
                "action": "get_stock_item_groups",
                "label": "Get all stock item groups and their associated supergroups",
                "enabled": False,
            },
            {
                "action": "get_timeclock_entries",
                "label": "Get timeclock entries for a specified time period with filtering options",
                "enabled": True,
            },
            {
                "action": "get_staff_members",
                "label": "Get a list of staff members with optional inclusion of deleted members and last clock times",
                "enabled": True,
            },
        ],
        "enabled": True,
    },
    # ── Email bindings ── all agents get email capabilities
    {
        "agent_slug": "procurement",
        "connector_name": "gmail",
        "capabilities": [
            {
                "action": "send_email",
                "label": "Send email from user's Gmail",
                "enabled": True,
            },
        ],
    },
    {
        "agent_slug": "procurement",
        "connector_name": "microsoft_outlook",
        "capabilities": [
            {
                "action": "send_email",
                "label": "Send email from user's Outlook",
                "enabled": True,
            },
        ],
    },
    {
        "agent_slug": "procurement",
        "connector_name": "norm_email",
        "capabilities": [
            {
                "action": "send_notification",
                "label": "Send system notification email",
                "enabled": True,
            },
        ],
    },
    {
        "agent_slug": "hr",
        "connector_name": "gmail",
        "capabilities": [
            {
                "action": "send_email",
                "label": "Send email from user's Gmail",
                "enabled": True,
            },
        ],
    },
    {
        "agent_slug": "hr",
        "connector_name": "microsoft_outlook",
        "capabilities": [
            {
                "action": "send_email",
                "label": "Send email from user's Outlook",
                "enabled": True,
            },
        ],
    },
    {
        "agent_slug": "hr",
        "connector_name": "norm_email",
        "capabilities": [
            {
                "action": "send_notification",
                "label": "Send system notification email",
                "enabled": True,
            },
        ],
    },
    {
        "agent_slug": "reports",
        "connector_name": "gmail",
        "capabilities": [
            {
                "action": "send_email",
                "label": "Send email from user's Gmail",
                "enabled": True,
            },
        ],
    },
    {
        "agent_slug": "reports",
        "connector_name": "microsoft_outlook",
        "capabilities": [
            {
                "action": "send_email",
                "label": "Send email from user's Outlook",
                "enabled": True,
            },
        ],
    },
    {
        "agent_slug": "reports",
        "connector_name": "norm_email",
        "capabilities": [
            {
                "action": "send_notification",
                "label": "Send system notification email",
                "enabled": True,
            },
        ],
    },
]
