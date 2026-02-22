export type WSMessageType =
  | 'agent_status'
  | 'activity'
  | 'task_update'
  | 'gate_created'
  | 'gate_resolved'
  | 'tool_executing'
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
