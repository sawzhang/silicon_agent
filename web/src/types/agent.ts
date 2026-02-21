export interface TokenUsage {
  prompt_tokens: number;
  completion_tokens: number;
  total_tokens: number;
  cost_usd: number;
}

export interface AgentStatus {
  role: string;
  status: 'running' | 'idle' | 'waiting' | 'error' | 'stopped';
  model: string;
  current_task_id: string | null;
  current_stage: string | null;
  token_usage: TokenUsage;
  uptime_seconds: number;
  last_heartbeat: string;
  error_message: string | null;
}

export interface AgentListResponse {
  agents: AgentStatus[];
  total: number;
}

export interface AgentConfigUpdate {
  model?: string;
  temperature?: number;
  max_tokens?: number;
  enabled?: boolean;
}
