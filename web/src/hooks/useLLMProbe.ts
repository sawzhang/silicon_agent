import { useMutation } from '@tanstack/react-query';
import { probeLLM } from '@/services/llmProbeApi';
import type { LLMProbeRequest } from '@/types/llmProbe';

/**
 * Mutation hook for manually triggering LLM quick probe.
 * @returns Mutation object with probe trigger and latest response state.
 */
export function useLLMProbe() {
  return useMutation({
    mutationFn: (params?: LLMProbeRequest) => probeLLM(params),
  });
}
