import api from './api';

export interface TaskLogEvent {
  id: string;
  task_id: string;
  stage_id: string | null;
  stage_name: string;
  agent_role: string | null;
  correlation_id: string | null;
  event_seq: number;
  event_type: string;
  event_source: 'llm' | 'tool' | string;
  status: string;
  request_body: Record<string, unknown> | null;
  response_body: Record<string, unknown> | null;
  command: string | null;
  command_args: Record<string, unknown> | null;
  workspace: string | null;
  execution_mode: 'sandbox' | 'in_process' | null;
  duration_ms: number | null;
  result: string | null;
  output_summary: string | null;
  output_truncated: boolean;
  missing_fields: string[];
  created_at: string;
}

export interface TaskLogListResponse {
  items: TaskLogEvent[];
  total: number;
  page: number;
  page_size: number;
}

export async function listTaskLogs(params: {
  task: string;
  task_id?: string;
  stage?: string;
  event_source?: string;
  page?: number;
  page_size?: number;
}): Promise<TaskLogListResponse> {
  const { data } = await api.get<TaskLogListResponse>('/task-logs', { params });
  return data;
}
