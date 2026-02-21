import { useQuery } from '@tanstack/react-query';
import { listAgents, getAgent } from '@/services/agentApi';

export function useAgentList() {
  return useQuery({
    queryKey: ['agents'],
    queryFn: listAgents,
    refetchInterval: 15_000,
  });
}

export function useAgent(role: string) {
  return useQuery({
    queryKey: ['agent', role],
    queryFn: () => getAgent(role),
    enabled: !!role,
  });
}
