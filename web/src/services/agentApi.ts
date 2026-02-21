import api from './api';
import type { AgentStatus, AgentListResponse, AgentConfigUpdate } from '@/types/agent';

export async function listAgents(): Promise<AgentListResponse> {
  const { data } = await api.get<AgentListResponse>('/agents');
  return data;
}

export async function getAgent(role: string): Promise<AgentStatus> {
  const { data } = await api.get<AgentStatus>(`/agents/${role}`);
  return data;
}

export async function updateConfig(role: string, config: AgentConfigUpdate): Promise<AgentStatus> {
  const { data } = await api.patch<AgentStatus>(`/agents/${role}/config`, config);
  return data;
}

export async function startAgent(role: string): Promise<void> {
  await api.post(`/agents/${role}/start`);
}

export async function stopAgent(role: string): Promise<void> {
  await api.post(`/agents/${role}/stop`);
}
