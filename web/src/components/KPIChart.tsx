import React from 'react';
import ReactECharts from 'echarts-for-react';
import type { KPITimeSeries } from '@/types/kpi';

interface KPIChartProps {
  data: KPITimeSeries;
  type?: 'line' | 'bar';
  height?: number;
}

const KPIChart: React.FC<KPIChartProps> = ({ data, type = 'line', height = 300 }) => {
  const option = {
    tooltip: { trigger: 'axis' as const },
    xAxis: {
      type: 'category' as const,
      data: data.points.map((p) => p.timestamp),
      axisLabel: {
        formatter: (val: string) => {
          const d = new Date(val);
          return `${d.getMonth() + 1}/${d.getDate()} ${d.getHours()}:00`;
        },
      },
    },
    yAxis: {
      type: 'value' as const,
      name: data.unit,
    },
    series: [
      {
        name: data.display_name,
        type,
        data: data.points.map((p) => p.value),
        smooth: type === 'line',
        areaStyle: type === 'line' ? { opacity: 0.1 } : undefined,
      },
    ],
    grid: { left: 60, right: 20, top: 40, bottom: 40 },
  };

  return <ReactECharts option={option} style={{ height }} />;
};

export default KPIChart;
