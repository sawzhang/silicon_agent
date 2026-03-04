import api from './api';
import type {
  TriggerRule,
  TriggerEvent,
  TriggerRuleCreate,
  TriggerRuleUpdate,
  TriggerTestRequest,
  TriggerTestResult,
  MockWebhookRequest,
  MockWebhookResponse,
} from '@/types/trigger';

// ── 全局触发规则 API ─────────────────────────────────

export async function listRules(): Promise<TriggerRule[]> {
  const { data } = await api.get<TriggerRule[]>('/triggers');
  return data;
}

export async function createRule(req: TriggerRuleCreate): Promise<TriggerRule> {
  const { data } = await api.post<TriggerRule>('/triggers', req);
  return data;
}

export async function updateRule(ruleId: string, req: TriggerRuleUpdate): Promise<TriggerRule> {
  const { data } = await api.put<TriggerRule>(`/triggers/${ruleId}`, req);
  return data;
}

export async function deleteRule(ruleId: string): Promise<void> {
  await api.delete(`/triggers/${ruleId}`);
}

export async function testRule(ruleId: string, req: TriggerTestRequest): Promise<TriggerTestResult> {
  const { data } = await api.post<TriggerTestResult>(`/triggers/${ruleId}/test`, req);
  return data;
}

// ── 项目级触发规则 & 事件 API ─────────────────────────

export async function listProjectRules(projectId: string): Promise<TriggerRule[]> {
  const { data } = await api.get<TriggerRule[]>(`/projects/${projectId}/triggers`);
  return data;
}

export async function listProjectEvents(
  projectId: string,
  limit: number = 50,
): Promise<TriggerEvent[]> {
  const { data } = await api.get<TriggerEvent[]>(`/projects/${projectId}/triggers/events`, {
    params: { limit },
  });
  return data;
}

// ── Mock Webhook API ─────────────────────────────────

export async function mockWebhook(
  projectId: string,
  req: MockWebhookRequest,
): Promise<MockWebhookResponse> {
  const { data } = await api.post<MockWebhookResponse>(
    `/projects/${projectId}/mock-webhook`,
    req,
  );
  return data;
}
