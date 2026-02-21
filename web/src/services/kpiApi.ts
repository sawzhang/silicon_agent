import api from './api';
import type { KPISummary, KPITimeSeries } from '@/types/kpi';

export async function getKPISummary(period?: string): Promise<KPISummary> {
  const { data } = await api.get<KPISummary>('/kpi/summary', {
    params: period ? { period } : undefined,
  });
  return data;
}

export async function getKPITimeSeries(
  metric: string,
  params?: { period?: string; interval?: string },
): Promise<KPITimeSeries> {
  const { data } = await api.get<KPITimeSeries>(`/kpi/timeseries/${metric}`, { params });
  return data;
}

export async function getKPIReport(period?: string): Promise<Blob> {
  const { data } = await api.get('/kpi/report', {
    params: period ? { period } : undefined,
    responseType: 'blob',
  });
  return data;
}

export async function getPSPCompare(): Promise<Record<string, unknown>> {
  const { data } = await api.get('/kpi/psp-compare');
  return data;
}
