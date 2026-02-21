import React from 'react';
import { Card, Statistic } from 'antd';
import { ArrowUpOutlined, ArrowDownOutlined, MinusOutlined } from '@ant-design/icons';
import type { KPIMetricValue } from '@/types/kpi';

interface KPICardProps {
  metric: KPIMetricValue;
}

const TREND_ICONS: Record<string, React.ReactNode> = {
  up: <ArrowUpOutlined />,
  down: <ArrowDownOutlined />,
  flat: <MinusOutlined />,
};

const KPICard: React.FC<KPICardProps> = ({ metric }) => {
  const trendColor = metric.trend === 'up' ? '#3f8600' : metric.trend === 'down' ? '#cf1322' : '#666';

  return (
    <Card size="small">
      <Statistic
        title={metric.display_name}
        value={metric.value}
        suffix={metric.unit}
        prefix={TREND_ICONS[metric.trend]}
        valueStyle={{ color: trendColor }}
      />
      <div style={{ marginTop: 4, fontSize: 12, color: '#999' }}>
        Target: {metric.target}{metric.unit} | Change: {metric.change_percent > 0 ? '+' : ''}{metric.change_percent.toFixed(1)}%
      </div>
    </Card>
  );
};

export default KPICard;
