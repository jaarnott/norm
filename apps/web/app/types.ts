// --- Connector Spec types ---

export interface ConnectorSpecSummary {
  id: string;
  connector_name: string;
  display_name: string;
  category: string | null;
  execution_mode: 'template' | 'agent';
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

export interface BaseTask {
  id: string;
  domain: string;
  intent: string;
  title: string | null;
  message: string;
  status: string;
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
}

export interface ProcurementTask extends BaseTask {
  domain: 'procurement';
  venue: { id: string; name: string } | null;
  product: { id: string; name: string; unit: string; category: string } | null;
  supplier: string | null;
  quantity: number | null;
  line_summary: string | null;
}

export interface HrTask extends BaseTask {
  domain: 'hr';
  employee_name: string | null;
  venue: { id: string; name: string } | null;
  role: string | null;
  start_date: string | null;
  missing_fields: string[];
  checklist: { item: string; done: boolean }[];
}

export interface ReportsTask extends BaseTask {
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

export type Task = ProcurementTask | HrTask | ReportsTask | BaseTask;

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
  is_custom_prompt: boolean;
  enabled: boolean;
  bindings: AgentBinding[];
  available_connectors: AvailableConnector[];
}
