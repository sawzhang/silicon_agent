import api from './api';
import type { Project, ProjectListResponse, ProjectCreateRequest, ProjectUpdateRequest, ProjectSyncResponse } from '@/types/project';

export async function listProjects(params?: {
  page?: number;
  page_size?: number;
  status?: string;
}): Promise<ProjectListResponse> {
  const { data } = await api.get<ProjectListResponse>('/projects', { params });
  return data;
}

export async function getProject(id: string): Promise<Project> {
  const { data } = await api.get<Project>(`/projects/${id}`);
  return data;
}

export async function createProject(req: ProjectCreateRequest): Promise<Project> {
  const { data } = await api.post<Project>('/projects', req);
  return data;
}

export async function updateProject(id: string, req: ProjectUpdateRequest): Promise<Project> {
  const { data } = await api.put<Project>(`/projects/${id}`, req);
  return data;
}

export async function deleteProject(id: string): Promise<void> {
  await api.delete(`/projects/${id}`);
}

export async function syncProject(id: string): Promise<ProjectSyncResponse> {
  const { data } = await api.post<ProjectSyncResponse>(`/projects/${id}/sync`);
  return data;
}
