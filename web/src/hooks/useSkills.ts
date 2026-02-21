import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { listSkills, getSkill, createSkill, updateSkill, archiveSkill } from '@/services/skillApi';
import type { SkillCreateRequest, SkillUpdateRequest } from '@/types/skill';

export function useSkillList(params?: { layer?: string; tag?: string; role?: string }) {
  return useQuery({
    queryKey: ['skills', params],
    queryFn: () => listSkills(params),
  });
}

export function useSkill(name: string) {
  return useQuery({
    queryKey: ['skill', name],
    queryFn: () => getSkill(name),
    enabled: !!name,
  });
}

export function useCreateSkill() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (req: SkillCreateRequest) => createSkill(req),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['skills'] });
    },
  });
}

export function useUpdateSkill() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ name, req }: { name: string; req: SkillUpdateRequest }) => updateSkill(name, req),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['skills'] });
    },
  });
}

export function useArchiveSkill() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (name: string) => archiveSkill(name),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['skills'] });
    },
  });
}
