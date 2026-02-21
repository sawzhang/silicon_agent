import api from './api';
import type { Gate, GateApproveRequest, GateRejectRequest } from '@/types/gate';

export async function listGates(params?: {
  status?: string;
  task_id?: string;
}): Promise<Gate[]> {
  const { data } = await api.get<Gate[]>('/gates', { params });
  return data;
}

export async function getGate(id: string): Promise<Gate> {
  const { data } = await api.get<Gate>(`/gates/${id}`);
  return data;
}

export async function approveGate(id: string, req?: GateApproveRequest): Promise<Gate> {
  const { data } = await api.post<Gate>(`/gates/${id}/approve`, req ?? {});
  return data;
}

export async function rejectGate(id: string, req: GateRejectRequest): Promise<Gate> {
  const { data } = await api.post<Gate>(`/gates/${id}/reject`, req);
  return data;
}

export async function getGateHistory(params?: {
  page?: number;
  page_size?: number;
}): Promise<Gate[]> {
  const { data } = await api.get<Gate[]>('/gates/history', { params });
  return data;
}
