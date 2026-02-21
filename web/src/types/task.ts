export interface TaskStage {
  name: string;
  status: 'pending' | 'running' | 'completed' | 'failed' | 'skipped';
  agent_role: string;
  started_at: string | null;
  completed_at: string | null;
  duration_seconds: number | null;
  token_usage: {
    prompt_tokens: number;
    completion_tokens: number;
    total_tokens: number;
    cost_usd: number;
  } | null;
  output_summary: string | null;
  error_message: string | null;
}

export interface Task {
  id: string;
  title: string;
  description: string;
  status: 'queued' | 'running' | 'completed' | 'failed' | 'cancelled';
  priority: 'low' | 'medium' | 'high' | 'critical';
  created_at: string;
  started_at: string | null;
  completed_at: string | null;
  stages: TaskStage[];
  total_tokens: number;
  total_cost_usd: number;
  created_by: string;
}

export interface TaskListResponse {
  tasks: Task[];
  total: number;
  page: number;
  page_size: number;
}

export interface TaskCreateRequest {
  title: string;
  description: string;
  priority?: 'low' | 'medium' | 'high' | 'critical';
}
