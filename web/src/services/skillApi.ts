import api from './api';
import type { Skill, SkillCreateRequest, SkillUpdateRequest } from '@/types/skill';

export async function listSkills(params?: {
  layer?: string;
  tag?: string;
  role?: string;
}): Promise<Skill[]> {
  const { data } = await api.get<Skill[]>('/skills', { params });
  return data;
}

export async function createSkill(req: SkillCreateRequest): Promise<Skill> {
  const { data } = await api.post<Skill>('/skills', req);
  return data;
}

export async function getSkill(name: string): Promise<Skill> {
  const { data } = await api.get<Skill>(`/skills/${name}`);
  return data;
}

export async function updateSkill(name: string, req: SkillUpdateRequest): Promise<Skill> {
  const { data } = await api.patch<Skill>(`/skills/${name}`, req);
  return data;
}

export async function archiveSkill(name: string): Promise<void> {
  await api.delete(`/skills/${name}`);
}
