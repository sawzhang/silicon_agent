import api from './api';

export interface AuditLog {
  id: string;
  timestamp: string;
  role: string;
  action: string;
  detail: string;
  risk_level: 'low' | 'medium' | 'high' | 'critical';
  task_id: string | null;
  stage: string | null;
}

export interface AuditListResponse {
  logs: AuditLog[];
  total: number;
  page: number;
  page_size: number;
}

interface BackendAuditLog {
  id: string;
  agent_role: string;
  action_type: string;
  action_detail: Record<string, unknown> | null;
  risk_level: string;
  created_at: string;
}

function mapAuditLog(raw: BackendAuditLog): AuditLog {
  return {
    id: raw.id,
    timestamp: raw.created_at,
    role: raw.agent_role,
    action: raw.action_type,
    detail: raw.action_detail ? JSON.stringify(raw.action_detail) : '',
    risk_level: raw.risk_level as AuditLog['risk_level'],
    task_id: null,
    stage: null,
  };
}

export async function listAuditLogs(params?: {
  role?: string;
  risk_level?: string;
  start_date?: string;
  end_date?: string;
  page?: number;
  page_size?: number;
}): Promise<AuditListResponse> {
  const { data } = await api.get<{ items: BackendAuditLog[]; total: number; page: number; page_size: number }>('/audit/logs', { params });
  return {
    logs: data.items.map(mapAuditLog),
    total: data.total,
    page: data.page,
    page_size: data.page_size,
  };
}

export async function getAuditLog(id: string): Promise<AuditLog> {
  const { data } = await api.get<BackendAuditLog>(`/audit/logs/${id}`);
  return mapAuditLog(data);
}
