import { useQuery } from '@tanstack/react-query';
import { getKPISummary, getKPITimeSeries, getROISummary, getCockpit } from '@/services/kpiApi';

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

export function useROISummary(days?: number) {
  return useQuery({
    queryKey: ['roi-summary', days],
    queryFn: () => getROISummary(days),
  });
}

export function useCockpit() {
  return useQuery({
    queryKey: ['cockpit'],
    queryFn: getCockpit,
    refetchInterval: 10_000,
  });
}
