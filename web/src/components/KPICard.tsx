import React from 'react';
import { Card, Statistic } from 'antd';
import type { KPIMetricValue } from '@/types/kpi';

interface KPICardProps {
  metric: KPIMetricValue;
}

const KPICard: React.FC<KPICardProps> = ({ metric }) => {
  return (
    <Card size="small">
      <Statistic
        title={metric.metric_name}
        value={metric.value}
        suffix={metric.unit}
      />
      {metric.agent_role && (
        <div style={{ marginTop: 4, fontSize: 12, color: '#999' }}>
          Agent: {metric.agent_role}
        </div>
      )}
    </Card>
  );
};

export default KPICard;
