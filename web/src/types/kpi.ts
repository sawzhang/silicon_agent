export interface KPIMetricValue {
  metric_name: string;
  value: number;
  unit: string;
  agent_role: string | null;
  recorded_at: string | null;
}

export interface KPISummary {
  total_tasks: number;
  completed_tasks: number;
  success_rate: number;
  avg_duration_minutes: number;
  total_tokens: number;
  total_cost_rmb: number;
  metrics: KPIMetricValue[];
}

export interface KPITimeSeriesPoint {
  timestamp: string;
  value: number;
}

export interface KPITimeSeries {
  metric_name: string;
  unit: string;
  data: KPITimeSeriesPoint[];
}

// ── ROI Dashboard ──────────────────────────────────────

export interface ROITaskBreakdown {
  task_id: string;
  title: string;
  agent_cost_rmb: number;
  estimated_manual_rmb: number;
  savings_rmb: number;
  agent_duration_minutes: number;
  estimated_manual_hours: number;
}

export interface AgentRoleEfficiency {
  role: string;
  display_name: string;
  total_stages: number;
  total_tokens: number;
  avg_duration_seconds: number;
  total_cost_rmb: number;
}

export interface ROISummary {
  total_tasks_completed: number;
  total_agent_cost_rmb: number;
  total_estimated_manual_rmb: number;
  total_savings_rmb: number;
  roi_ratio: number;
  total_agent_hours: number;
  total_estimated_manual_hours: number;
  time_saved_hours: number;
  benchmark_hours_per_task: number;
  benchmark_hourly_rate: number;
  by_role: AgentRoleEfficiency[];
  recent_tasks: ROITaskBreakdown[];
}

// ── Developer Cockpit ──────────────────────────────────

export interface CockpitTaskItem {
  id: string;
  title: string;
  status: string;
  project_name: string | null;
  template_name: string | null;
  created_at: string;
  completed_at: string | null;
  current_stage: string | null;
  error_message: string | null;
  total_tokens: number;
  total_cost_rmb: number;
}

export interface CockpitData {
  pending_gates_count: number;
  running_tasks_count: number;
  failed_tasks_today: number;
  completed_tasks_today: number;
  pending_gates: Array<{
    id: string;
    gate_type: string;
    task_id: string;
    agent_role: string;
    content: Record<string, string> | null;
    status: 'pending' | 'approved' | 'rejected';
    reviewer: string | null;
    review_comment: string | null;
    created_at: string;
    reviewed_at: string | null;
  }>;
  running_tasks: CockpitTaskItem[];
  failed_tasks: CockpitTaskItem[];
  recent_completed: CockpitTaskItem[];
}
