export interface KPIMetricValue {
  name: string;
  display_name: string;
  value: number;
  target: number;
  unit: string;
  trend: 'up' | 'down' | 'flat';
  change_percent: number;
}

export interface KPISummary {
  metrics: KPIMetricValue[];
  period: string;
  updated_at: string;
}

export interface KPITimeSeriesPoint {
  timestamp: string;
  value: number;
}

export interface KPITimeSeries {
  metric_name: string;
  display_name: string;
  unit: string;
  points: KPITimeSeriesPoint[];
  period: string;
}
