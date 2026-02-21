import api from './api';
import type { TaskTemplate, TemplateListResponse } from '@/types/template';

export async function listTemplates(): Promise<TemplateListResponse> {
  const { data } = await api.get<TemplateListResponse>('/templates');
  return data;
}

export async function getTemplate(id: string): Promise<TaskTemplate> {
  const { data } = await api.get<TaskTemplate>(`/templates/${id}`);
  return data;
}
