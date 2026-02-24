import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { listProjects, createProject, updateProject, deleteProject } from '@/services/projectApi';
import type { ProjectCreateRequest, ProjectUpdateRequest } from '@/types/project';

export function useProjectList(params?: {
  page?: number;
  page_size?: number;
  status?: string;
  name?: string;
}) {
  return useQuery({
    queryKey: ['projects', params],
    queryFn: () => listProjects(params),
  });
}

export function useCreateProject() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (req: ProjectCreateRequest) => createProject(req),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['projects'] });
    },
  });
}

export function useUpdateProject() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ id, req }: { id: string; req: ProjectUpdateRequest }) => updateProject(id, req),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['projects'] });
    },
  });
}

export function useDeleteProject() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (id: string) => deleteProject(id),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['projects'] });
    },
  });
}
