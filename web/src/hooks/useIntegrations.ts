import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import {
  listIntegrations,
  createIntegration,
  updateIntegration,
  deleteIntegration,
  regenerateSecret,
} from '@/services/integrationApi';
import type { IntegrationCreateRequest, IntegrationUpdateRequest } from '@/types/integration';

export function useIntegrationList(projectId: string) {
  return useQuery({
    queryKey: ['integrations', projectId],
    queryFn: () => listIntegrations(projectId),
    enabled: !!projectId,
  });
}

export function useCreateIntegration(projectId: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (req: IntegrationCreateRequest) => createIntegration(projectId, req),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['integrations', projectId] });
    },
  });
}

export function useUpdateIntegration(projectId: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ provider, req }: { provider: string; req: IntegrationUpdateRequest }) =>
      updateIntegration(projectId, provider, req),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['integrations', projectId] });
    },
  });
}

export function useDeleteIntegration(projectId: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (provider: string) => deleteIntegration(projectId, provider),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['integrations', projectId] });
    },
  });
}

export function useRegenerateSecret(projectId: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (provider: string) => regenerateSecret(projectId, provider),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['integrations', projectId] });
    },
  });
}
