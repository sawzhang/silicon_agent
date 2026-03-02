export interface TriggerRule {
  id: string;
  name: string;
  source: string;
  event_type: string;
  filters: Record<string, unknown> | null;
  template_id: string | null;
  project_id: string | null;
  title_template: string;
  desc_template: string | null;
  dedup_key_template: string | null;
  dedup_window_hours: number;
  cron_expr: string | null;
  last_triggered_at: string | null;
  enabled: boolean;
  created_at: string;
}

export interface TriggerEvent {
  id: string;
  rule_id: string | null;
  source: string;
  event_type: string;
  project_id: string | null;
  task_id: string | null;
  dedup_key: string | null;
  result: 'triggered' | 'skipped_no_rule' | 'skipped_filter' | 'skipped_dedup';
  created_at: string;
}

export interface TriggerRuleCreate {
  name: string;
  source: string;
  event_type: string;
  filters?: Record<string, unknown>;
  template_id?: string;
  project_id?: string;
  title_template?: string;
  desc_template?: string;
  dedup_key_template?: string;
  dedup_window_hours?: number;
  cron_expr?: string;
  enabled?: boolean;
}

export interface TriggerRuleUpdate {
  name?: string;
  source?: string;
  event_type?: string;
  filters?: Record<string, unknown>;
  template_id?: string;
  project_id?: string;
  title_template?: string;
  desc_template?: string;
  dedup_key_template?: string;
  dedup_window_hours?: number;
  cron_expr?: string;
  enabled?: boolean;
}

export interface TriggerTestRequest {
  payload: Record<string, unknown>;
}

export interface TriggerTestResult {
  rule_id: string;
  rule_name: string;
  filter_passed: boolean;
  dedup_blocked: boolean;
  dedup_key: string | null;
  rendered_title: string;
  rendered_desc: string | null;
  would_trigger: boolean;
  result: string;
}
