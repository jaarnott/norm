// --- Connector Spec types ---

export interface ConnectorSpecSummary {
  id: string;
  connector_name: string;
  display_name: string;
  category: string | null;
  execution_mode: 'template' | 'agent' | 'internal';
  auth_type: string;
  version: number;
  enabled: boolean;
  created_at: string;
  updated_at: string | null;
}

export interface ConnectorSpecTool {
  action: string;
  method: string;
  path_template: string;
  headers: Record<string, string>;
  required_fields: string[];
  field_mapping: Record<string, string>;
  field_descriptions: Record<string, string>;
  field_schema?: Record<string, unknown> | null;
  request_body_template: string | null;
  success_status_codes: number[];
  response_ref_path: string | null;
  timeout_seconds: number;
  description?: string;
  display_component?: string | null;
  display_props?: Record<string, unknown> | null;
  working_document?: { doc_type: string; sync_mode: string; ref_fields: string[] } | null;
  summary_fields?: string[] | null;
  response_transform?: { enabled: boolean; fields: Record<string, string>; flatten?: string[]; filters?: { field: string; operator: string; value: string }[] } | null;
  consolidator_config?: Record<string, unknown> | null;
}

export interface BillingInfo {
  subscription: {
    token_plan: 'basic' | 'standard' | 'max' | null;
    token_quota: number;
    status: 'active' | 'past_due' | 'canceled' | 'trialing' | null;
    billing_cycle_start: string | null;
    payment_method_last4: string | null;
    payment_method_brand: string | null;
  } | null;
  usage: {
    allowed: boolean;
    used: number;
    quota: number;
    remaining: number;
  };
  agents: {
    hr: boolean;
    procurement: boolean;
    reports: boolean;
  };
  venue_count: number;
  monthly_cost_cents: number;
  cost_breakdown: {
    plan: number;
    agents: number;
    venues: number;
  };
}

export interface StripeInvoice {
  id: string;
  amount_due: number;
  amount_paid: number;
  currency: string;
  status: string;
  created: number;
  invoice_pdf: string | null;
  hosted_invoice_url: string | null;
}

export type ChartType = 'bar' | 'stacked_bar' | 'line' | 'pie' | 'scatter' | 'bubble' | 'table';

export interface ChartSpec {
  chart_type: ChartType;
  title: string;
  x_axis: { key: string; label: string };
  y_axis?: { key: string; label: string };
  series: { key: string; label: string; color: string }[];
  orientation?: 'vertical' | 'horizontal';
}

export interface ChartScript {
  connector: string;
  action: string;
  params: Record<string, unknown>;
}

export interface ReportGridItem {
  chart_id: string;
  col: number;      // 1-based, 1-12
  row: number;      // 1-based, auto-expanding
  colSpan: number;  // 1-12
  rowSpan: number;  // 1+
}

export interface SavedReport {
  id: string;
  title: string;
  description: string | null;
  layout: ReportGridItem[];
  status: string;
  charts: SavedReportChart[];
  created_at: string;
  updated_at: string;
}

export interface SavedReportChart {
  id: string;
  title: string;
  chart_type: ChartType;
  chart_spec: ChartSpec;
  data: Record<string, unknown>[];
  script: ChartScript;
  position: number;
  created_at: string;
}

export interface OAuthConfig {
  authorize_url: string;
  token_url: string;
  scopes: string;
  client_id: string;
  client_secret: string;
}

export interface TestRequest {
  method: string;
  path_template: string;
  headers: Record<string, string>;
  success_status_codes: number[];
  timeout_seconds: number;
}

export interface ComponentApiConfig {
  id: string;
  component_key: string;
  connector_name: string;
  action_name: string;
  display_label: string | null;
  method: string;
  path_template: string;
  request_body_template: string | null;
  headers: Record<string, string>;
  required_fields: string[];
  field_descriptions: Record<string, string>;
  field_mapping: Record<string, string> | null;
  ref_fields: Record<string, string> | null;
  id_field: string | null;
  response_field_mapping: Record<string, string> | null;
  enabled: boolean;
  created_at: string;
  updated_at: string | null;
}

export interface ConnectorSpecFull extends ConnectorSpecSummary {
  auth_config: Record<string, unknown>;
  base_url_template: string | null;
  tools: ConnectorSpecTool[];
  api_documentation: string | null;
  example_requests: Record<string, unknown>[];
  credential_fields: { key: string; label: string; secret: boolean }[];
  oauth_config: OAuthConfig | null;
  test_request: TestRequest | null;
}

// --- LLM Call types ---

export interface ToolCallRecord {
  id: string;
  iteration: number;
  tool_name: string;
  connector_name: string;
  action: string;
  method: string;
  input_params: Record<string, unknown> | null;
  status: 'pending' | 'executed' | 'pending_approval' | 'approved' | 'rejected' | 'failed';
  result_payload: Record<string, unknown> | null;
  slimmed_content: string | null;
  error_message: string | null;
  duration_ms: number | null;
  rendered_request: Record<string, unknown> | null;
  created_at: string;
}

export interface LlmCall {
  id: string;
  call_type: 'routing' | 'interpretation' | 'execution' | 'spec_generation' | 'tool_use';
  model: string;
  system_prompt: string;
  user_prompt: string;
  raw_response: string | null;
  parsed_response: Record<string, unknown> | null;
  status: 'success' | 'error';
  error_message: string | null;
  duration_ms: number | null;
  input_tokens: number | null;
  output_tokens: number | null;
  tools_provided: Record<string, unknown>[] | null;
  created_at: string;
}

export interface DisplayBlock {
  component: string;
  data: Record<string, unknown>;
  props?: Record<string, unknown>;
}

export interface WidgetAction {
  connector_name: string;
  action: string;
  params: Record<string, unknown>;
}

export interface ConversationMessage {
  role: 'user' | 'assistant' | 'streaming';
  text: string;
  created_at?: string | null;
  display_blocks?: DisplayBlock[];
}

export interface BaseThread {
  id: string;
  domain: string;
  intent: string;
  title: string | null;
  message: string;
  status: string;
  tags: string[];
  created_at: string;
  clarification_question?: string | null;
  conversation?: ConversationMessage[];
  llm_calls?: LlmCall[];
  tool_calls?: ToolCallRecord[];
  thinking_steps?: string[];
  integration_run?: {
    connector: string;
    status: string;
    reference: string | null;
    submitted_at: string;
    error: string | null;
  } | null;
  approval?: {
    action: string;
    performed_by: string;
    performed_at: string;
  } | null;
  automated_task?: {
    id: string;
    title: string;
    description: string | null;
    agent_slug: string;
    schedule_type: string;
    schedule_config: Record<string, unknown>;
    status: string;
    prompt: string;
    task_config: Record<string, unknown>;
    thread_summary: string | null;
    tool_filter: string[] | null;
    last_run_at: string | null;
  } | null;
}

export interface ProcurementThread extends BaseThread {
  domain: 'procurement';
  venue: { id: string; name: string } | null;
  product: { id: string; name: string; unit: string; category: string } | null;
  supplier: string | null;
  quantity: number | null;
  line_summary: string | null;
}

export interface HrThread extends BaseThread {
  domain: 'hr';
  employee_name: string | null;
  venue: { id: string; name: string } | null;
  role: string | null;
  start_date: string | null;
  missing_fields: string[];
  checklist: { item: string; done: boolean }[];
}

export interface ReportsThread extends BaseThread {
  domain: 'reports';
  report_type: string | null;
  data_sources: string[];
  metrics: string[];
  time_range: { start: string; end: string; label: string } | null;
  venue_name: string | null;
  product_name: string | null;
  group_by: string | null;
  report_plan: string[] | null;
  report_result: {
    report_type: string;
    group_by: string;
    period_count: number;
    totals: Record<string, number>;
    rows: Record<string, unknown>[];
    generated_at: string;
  } | null;
}

export type Thread = ProcurementThread | HrThread | ReportsThread | BaseThread;

export interface AgentBinding {
  connector_name: string;
  connector_label: string;
  capabilities: { action: string; label: string; enabled: boolean }[];
  enabled: boolean;
}

export interface AvailableConnector {
  connector_name: string;
  display_name: string;
}

export interface AgentConfig {
  slug: string;
  display_name: string;
  description: string;
  system_prompt: string;
  has_prompt: boolean;
  enabled: boolean;
  bindings: AgentBinding[];
  available_connectors: AvailableConnector[];
}

export interface AutomatedTask {
  id: string;
  title: string;
  description: string | null;
  agent_slug: string;
  prompt: string;
  schedule_type: 'manual' | 'hourly' | 'daily' | 'weekly' | 'monthly';
  schedule_config: Record<string, unknown>;
  status: 'active' | 'paused' | 'draft';
  created_by: string | null;
  task_config: Record<string, unknown>;
  thread_summary: string | null;
  overrides_next_run: Record<string, unknown> | null;
  tool_filter: string[] | null;
  conversation_thread_id: string | null;
  last_run_at: string | null;
  next_run_at: string | null;
  created_at: string;
  updated_at?: string;
}

export interface AutomatedTaskRun {
  id: string;
  automated_task_id: string;
  thread_id: string | null;
  status: 'running' | 'success' | 'error';
  mode: 'live' | 'test';
  result_summary: string | null;
  tool_calls_count: number;
  error_message: string | null;
  has_pending_approvals: boolean;
  started_at: string;
  completed_at: string | null;
  duration_ms: number | null;
}

export interface AutomatedTaskRunDetail extends AutomatedTaskRun {
  messages?: Array<{
    id: string;
    role: string;
    content: string;
    display_blocks: unknown[] | null;
    created_at: string;
  }>;
  tool_calls?: Array<{
    id: string;
    tool_name: string;
    connector_name: string;
    action: string;
    method: string;
    status: string;
    input_params: Record<string, unknown>;
    result_payload: unknown;
    slimmed_content: string | null;
    error_message: string | null;
    duration_ms: number | null;
    created_at: string;
  }>;
  pending_tool_call_ids?: string[] | null;
}

export interface Organization {
  id: string;
  name: string;
  slug: string;
  billing_email: string | null;
  plan: string;
  is_active: boolean;
  venue_count: number;
  member_count: number;
  created_at: string;
  venues?: VenueDetail[];
  members?: OrgMember[];
}

export interface VenueDetail {
  id: string;
  name: string;
  location: string | null;
  timezone: string | null;
  day_start_time: string | null;
  organization_id: string | null;
  connector_count?: number;
}

export interface OrgMember {
  id: string;
  user_id: string;
  email: string;
  full_name: string;
  role: string;
  role_id?: string | null;
  role_name?: string;
  role_display_name?: string;
  is_active?: boolean;
}

export interface User {
  id: string;
  email: string;
  full_name: string;
  role: string;
  permissions: string[];
  org_role: { name: string; display_name: string } | null;
}
