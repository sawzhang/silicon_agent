export type WSMessageType =
  | 'agent_status'
  | 'activity'
  | 'task_update'
  | 'stage_log'
  | 'gate_created'
  | 'gate_resolved'
  | 'task_log_stream'
  | 'pong';

export interface WSMessage {
  type: WSMessageType;
  payload: unknown;
  timestamp: string;
}

export interface WSAgentStatusPayload {
  role: string;
  status: string;
  current_task_id: string | null;
  current_stage: string | null;
}

export interface WSActivityPayload {
  id: string;
  role: string;
  action: string;
  detail: string;
  timestamp: string;
  level: 'info' | 'warning' | 'error';
}

export interface WSGatePayload {
  gate_id: string;
  task_id: string;
  stage: string;
  status: string;
}

export interface WSToolExecutingPayload {
  role: string;
  tool_name: string;
  task_id: string;
}

export interface WSStageLogPayload {
  task_id: string;
  stage_id: string;
  stage_name: string;
  event_type: string;
  event_source: string;
  status: string;
  command?: string;
  duration_ms?: number;
  result_preview?: string;
  timestamp: string;
}

export interface WSTaskLogStreamPayload {
  task_id: string;
  stage_id: string;
  stage_name: string;
  log_id: string;
  tool_call_id: string;
  chunk: string;
  finished: boolean;
  status?: string;
}
