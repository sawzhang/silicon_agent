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

export async function listAuditLogs(params?: {
  role?: string;
  risk_level?: string;
  start_date?: string;
  end_date?: string;
  page?: number;
  page_size?: number;
}): Promise<AuditListResponse> {
  const { data } = await api.get<AuditListResponse>('/audit', { params });
  return data;
}

export async function getAuditLog(id: string): Promise<AuditLog> {
  const { data } = await api.get<AuditLog>(`/audit/${id}`);
  return data;
}
