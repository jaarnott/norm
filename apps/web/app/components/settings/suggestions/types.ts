// GET /api/admin/suggestions (routers/suggestion_admin.py): Home's suggestions,
// tuned site-wide. Every value comes with its default, so the page can show
// what changed and put it back.

export type ValueKind = 'bool' | 'count' | 'number' | 'times' | 'percent' | 'days' | 'hours' | 'dollars' | 'text';

export interface Meta {
  can_undo: boolean;
  updated_by: string | null;
  updated_at: string | null;
}

export interface SettingRow {
  name: string;
  label: string;
  help: string;
  kind: ValueKind;
  group: 'switches' | 'slots' | 'scoring' | 'memory' | 'wording';
  min: number;
  max: number;
  default: unknown;
  value: unknown;
  changed: boolean;
}

export interface PromptRow extends Meta {
  id: string;
  member: string;
  app: string;
  text: string;
  tools: string[];
  kind: 'regular' | 'discover';
  permission: string | null;
  connection: string | null;
  days: number[] | null;
  part: 'morning' | 'afternoon' | 'evening' | null;
  month_start: boolean;
  multi_venue: boolean;
  weight: number;
  enabled: boolean;
  custom: boolean;
  changed: string[];
}

export interface ThresholdRow {
  name: string;
  label: string;
  kind: ValueKind;
  min: number;
  max: number;
  default: number;
  value: number;
  changed: boolean;
}

export interface TemplateRow {
  name: string;
  label: string;
  default: string;
  value: string;
  changed: boolean;
  placeholders: Record<string, string>;
  example: Record<string, string>;
}

export interface DetectorRow extends Meta {
  name: string;
  label: string;
  description: string;
  area: 'sales' | 'wages' | 'cogs' | 'data';
  cadence: 'daily' | 'weekly' | 'roster' | 'follow-up';
  audience: string;
  valued: boolean;
  enabled: boolean;
  weight: number;
  default_weight: number;
  lift: number;
  default_lift: number;
  members: string[];
  default_members: string[];
  thresholds: ThresholdRow[];
  templates: TemplateRow[];
  changed: string[];
  live: { count: number; last: string | null };
}

export interface SuggestionAdmin {
  settings: SettingRow[];
  settings_meta: Meta;
  wording_version: string;
  guidance_placeholders: Record<string, string>;
  prompts: PromptRow[];
  new_prompt: Omit<PromptRow, keyof Meta | 'custom' | 'changed'>;
  detectors: DetectorRow[];
  members: { slug: string; name: string }[];
  apps: { slug: string; name: string }[];
  permissions: string[];
}

export interface Candidate {
  id: string;
  kind: 'insight' | 'pending' | 'catalog' | 'regular' | 'discover';
  member: string;
  text: string;
  venue_name?: string | null;
  score: number | null;
  reason: string | null;
}

export interface Preview {
  venue: string | null;
  local_time: string;
  picked: Candidate[];
  others: Candidate[];
}
