import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import {
  listSkills,
  getSkill,
  createSkill,
  updateSkill,
  archiveSkill,
  getSkillVersions,
  rollbackSkill,
  getSkillStats,
  syncSkills,
} from '@/services/skillApi';
import type { SkillCreateRequest, SkillUpdateRequest } from '@/types/skill';

export function useSkillList(params?: { layer?: string; tag?: string; role?: string; status?: string }) {
  return useQuery({
    queryKey: ['skills', params],
    queryFn: () => listSkills(params),
    select: (res) => res.items,
  });
}

export function useSkill(name: string) {
  return useQuery({
    queryKey: ['skill', name],
    queryFn: () => getSkill(name),
    enabled: !!name,
  });
}

export function useSkillVersions(name: string) {
  return useQuery({
    queryKey: ['skillVersions', name],
    queryFn: () => getSkillVersions(name),
    enabled: !!name,
  });
}

export function useSkillStats() {
  return useQuery({
    queryKey: ['skillStats'],
    queryFn: () => getSkillStats(),
  });
}

export function useCreateSkill() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (req: SkillCreateRequest) => createSkill(req),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['skills'] });
      qc.invalidateQueries({ queryKey: ['skillStats'] });
    },
  });
}

export function useUpdateSkill() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ name, req }: { name: string; req: SkillUpdateRequest }) => updateSkill(name, req),
    onSuccess: (_data, variables) => {
      qc.invalidateQueries({ queryKey: ['skills'] });
      qc.invalidateQueries({ queryKey: ['skill', variables.name] });
      qc.invalidateQueries({ queryKey: ['skillVersions', variables.name] });
      qc.invalidateQueries({ queryKey: ['skillStats'] });
    },
  });
}

export function useArchiveSkill() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (name: string) => archiveSkill(name),
    onSuccess: (_data, name) => {
      qc.invalidateQueries({ queryKey: ['skills'] });
      qc.invalidateQueries({ queryKey: ['skill', name] });
      qc.invalidateQueries({ queryKey: ['skillVersions', name] });
      qc.invalidateQueries({ queryKey: ['skillStats'] });
    },
  });
}

export function useRollbackSkill() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ name, version }: { name: string; version: string }) => rollbackSkill(name, version),
    onSuccess: (_data, variables) => {
      qc.invalidateQueries({ queryKey: ['skills'] });
      qc.invalidateQueries({ queryKey: ['skill', variables.name] });
      qc.invalidateQueries({ queryKey: ['skillVersions', variables.name] });
    },
  });
}

export function useSyncSkills() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: () => syncSkills(),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['skills'] });
      qc.invalidateQueries({ queryKey: ['skillStats'] });
    },
  });
}
