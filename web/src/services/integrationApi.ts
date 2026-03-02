import api from './api';
import type { Integration, IntegrationCreateRequest, IntegrationUpdateRequest } from '@/types/integration';

export async function listIntegrations(projectId: string): Promise<Integration[]> {
  const { data } = await api.get<Integration[]>(`/projects/${projectId}/integrations`);
  return data;
}

export async function getIntegration(projectId: string, provider: string): Promise<Integration> {
  const { data } = await api.get<Integration>(`/projects/${projectId}/integrations/${provider}`);
  return data;
}

export async function createIntegration(
  projectId: string,
  req: IntegrationCreateRequest,
): Promise<Integration> {
  const { data } = await api.post<Integration>(`/projects/${projectId}/integrations`, req);
  return data;
}

export async function updateIntegration(
  projectId: string,
  provider: string,
  req: IntegrationUpdateRequest,
): Promise<Integration> {
  const { data } = await api.put<Integration>(
    `/projects/${projectId}/integrations/${provider}`,
    req,
  );
  return data;
}

export async function deleteIntegration(projectId: string, provider: string): Promise<void> {
  await api.delete(`/projects/${projectId}/integrations/${provider}`);
}

export async function regenerateSecret(
  projectId: string,
  provider: string,
): Promise<Integration> {
  const { data } = await api.post<Integration>(
    `/projects/${projectId}/integrations/${provider}/regenerate-secret`,
  );
  return data;
}
