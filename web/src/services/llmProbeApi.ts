import api from './api';
import type { LLMProbeRequest, LLMProbeResponse } from '@/types/llmProbe';

/**
 * Execute a low-cost LLM liveness probe.
 * @param params Optional probe query parameters.
 * @returns Normalized liveness diagnostics payload.
 */
export async function probeLLM(params?: LLMProbeRequest): Promise<LLMProbeResponse> {
  const { data } = await api.get<LLMProbeResponse>('/llm/probe', { params });
  return data;
}
