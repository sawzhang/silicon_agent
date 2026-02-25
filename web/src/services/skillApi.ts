import api from './api';
import type {
  Skill,
  SkillCreateRequest,
  SkillListResponse,
  SkillSyncResponse,
  SkillStatsResponse,
  SkillUpdateRequest,
  SkillVersionListResponse,
} from '@/types/skill';

export async function listSkills(params?: {
  page?: number;
  page_size?: number;
  name?: string;
  layer?: string;
  tag?: string;
  role?: string;
  status?: string;
}): Promise<SkillListResponse> {
  const { data } = await api.get<SkillListResponse>('/skills', { params });
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
  const { data } = await api.put<Skill>(`/skills/${name}`, req);
  return data;
}

export async function archiveSkill(name: string): Promise<void> {
  await api.delete(`/skills/${name}`);
}

export async function getSkillVersions(name: string): Promise<SkillVersionListResponse> {
  const { data } = await api.get<SkillVersionListResponse>(`/skills/${name}/versions`);
  return data;
}

export async function rollbackSkill(name: string, version: string): Promise<Skill> {
  const { data } = await api.post<Skill>(`/skills/${name}/rollback`, null, {
    params: { version },
  });
  return data;
}

export async function getSkillStats(): Promise<SkillStatsResponse> {
  const { data } = await api.get<SkillStatsResponse>('/skills/stats');
  return data;
}

export async function syncSkills(): Promise<SkillSyncResponse> {
  const { data } = await api.post<SkillSyncResponse>('/skills/sync');
  return data;
}
