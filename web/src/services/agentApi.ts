import api from './api';
import type { AgentStatus, AgentListResponse, AgentConfigUpdate, AgentConfigOptions } from '@/types/agent';

export async function listAgents(): Promise<AgentListResponse> {
  const { data } = await api.get<AgentListResponse>('/agents');
  return data;
}

export async function getAgent(role: string): Promise<AgentStatus> {
  const { data } = await api.get<AgentStatus>(`/agents/${role}`);
  return data;
}

export async function getAgentConfigOptions(): Promise<AgentConfigOptions> {
  const { data } = await api.get<AgentConfigOptions>('/agents/config/options');
  return data;
}

export async function updateConfig(role: string, config: AgentConfigUpdate): Promise<AgentStatus> {
  const { data } = await api.put<AgentStatus>(`/agents/${role}/config`, config);
  return data;
}

export async function startAgent(role: string): Promise<AgentStatus> {
  const { data } = await api.post<AgentStatus>(`/agents/${role}/start`);
  return data;
}

export async function stopAgent(role: string): Promise<AgentStatus> {
  const { data } = await api.post<AgentStatus>(`/agents/${role}/stop`);
  return data;
}
