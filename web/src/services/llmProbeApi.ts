import api from './api';
import type {
  LLMProbeRequest,
  LLMProbeResponse,
  LLMConfigResponse,
  LLMConfigUpdateRequest,
} from '@/types/llmProbe';

export async function probeLLM(params?: LLMProbeRequest): Promise<LLMProbeResponse> {
  const { data } = await api.get<LLMProbeResponse>('/llm/probe', { params });
  return data;
}

export async function getLLMConfig(): Promise<LLMConfigResponse> {
  const { data } = await api.get<LLMConfigResponse>('/llm/config');
  return data;
}

export async function updateLLMConfig(
  payload: LLMConfigUpdateRequest,
): Promise<LLMConfigResponse> {
  const { data } = await api.put<LLMConfigResponse>('/llm/config', payload);
  return data;
}
