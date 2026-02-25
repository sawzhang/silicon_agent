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
  temperature?: number;
  max_tokens?: number;
  max_turns?: number;
  thinking_level?: string;
  extra_skill_dirs?: string[];
  system_prompt_append?: string;
  enabled?: boolean;
}


export interface AgentConfigOptions {
  available_models: string[];
  thinking_levels: string[];
  role_defaults: Record<string, string>;
}

export interface AgentConfigFormValues {
  model_name?: string;
  temperature?: number;
  max_tokens?: number;
  max_turns?: number;
  thinking_level?: string;
  extra_skill_dirs?: string[];
  system_prompt_append?: string;
  enabled?: boolean;
}
