import { useQuery } from '@tanstack/react-query';
import { getKPISummary, getKPITimeSeries } from '@/services/kpiApi';

export function useKPISummary(period?: string) {
  return useQuery({
    queryKey: ['kpi-summary', period],
    queryFn: () => getKPISummary(period),
  });
}

export function useKPITimeSeries(metric: string, params?: { period?: string; interval?: string }) {
  return useQuery({
    queryKey: ['kpi-timeseries', metric, params],
    queryFn: () => getKPITimeSeries(metric, params),
    enabled: !!metric,
  });
}
