import api from './api';
import type { KPISummary, KPITimeSeries, ROISummary, CockpitData } from '@/types/kpi';

export async function getKPISummary(period?: string): Promise<KPISummary> {
  const { data } = await api.get<KPISummary>('/kpi/summary', {
    params: period ? { period } : undefined,
  });
  return data;
}

export async function getKPITimeSeries(
  metric: string,
  params?: { period?: string; agent_role?: string },
): Promise<KPITimeSeries> {
  const { data } = await api.get<KPITimeSeries>(`/kpi/metrics/${metric}`, { params });
  return data;
}

export async function getKPIReport(period?: string): Promise<Blob> {
  const { data } = await api.get('/kpi/report', {
    params: period ? { period } : undefined,
    responseType: 'blob',
  });
  return data;
}

export async function getROISummary(days?: number): Promise<ROISummary> {
  const { data } = await api.get<ROISummary>('/kpi/roi', {
    params: days ? { days } : undefined,
  });
  return data;
}

export async function getCockpit(): Promise<CockpitData> {
  const { data } = await api.get<CockpitData>('/kpi/cockpit');
  return data;
}
