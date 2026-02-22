export interface TokenUsage {
  input_tokens: number;
  output_tokens: number;
  total_tokens: number;
  estimated_cost_rmb: number;
}

export interface AgentStatus {
  id: string;
  role: string;
  display_name: string;
  status: string;
  model_name: string | null;
  config: Record<string, unknown> | null;
  current_task_id: string | null;
  started_at: string | null;
  last_active_at: string | null;
  created_at: string;
  updated_at: string;
}

export interface AgentListResponse {
  agents: AgentStatus[];
}

export interface AgentConfigUpdate {
  model_name?: string;
  config?: Record<string, unknown>;
}
