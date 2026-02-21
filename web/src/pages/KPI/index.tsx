import React, { useState } from 'react';
import { Row, Col, Typography, Spin, Select, Card, Empty } from 'antd';
import { useKPISummary, useKPITimeSeries } from '@/hooks/useKPI';
import KPICard from '@/components/KPICard';
import KPIChart from '@/components/KPIChart';
import { KPI_DEFINITIONS } from '@/utils/constants';

const { Title } = Typography;

const KPIPage: React.FC = () => {
  const [period, setPeriod] = useState('7d');
  const [selectedMetric, setSelectedMetric] = useState(KPI_DEFINITIONS[0].name);
  const { data: summary, isLoading: summaryLoading } = useKPISummary(period);
  const { data: timeseries, isLoading: tsLoading } = useKPITimeSeries(selectedMetric, { period });

  return (
    <div>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 16 }}>
        <Title level={4} style={{ margin: 0 }}>KPI Dashboard</Title>
        <Select
          value={period}
          onChange={setPeriod}
          style={{ width: 120 }}
          options={[
            { label: 'Today', value: '1d' },
            { label: '7 Days', value: '7d' },
            { label: '30 Days', value: '30d' },
            { label: '90 Days', value: '90d' },
          ]}
        />
      </div>

      {summaryLoading ? (
        <Spin size="large" style={{ display: 'block', margin: '40px auto' }} />
      ) : summary ? (
        <Row gutter={[16, 16]} style={{ marginBottom: 24 }}>
          {summary.metrics.map((m) => (
            <Col key={m.name} xs={12} sm={8} md={6}>
              <KPICard metric={m} />
            </Col>
          ))}
        </Row>
      ) : (
        <Empty description="No KPI data available" style={{ marginBottom: 24 }} />
      )}

      <Card
        title="Time Series"
        extra={
          <Select
            value={selectedMetric}
            onChange={setSelectedMetric}
            style={{ width: 180 }}
            options={KPI_DEFINITIONS.map((k) => ({ label: k.display_name, value: k.name }))}
          />
        }
      >
        {tsLoading ? (
          <Spin style={{ display: 'block', margin: '40px auto' }} />
        ) : timeseries ? (
          <KPIChart data={timeseries} height={400} />
        ) : (
          <Empty description="No time series data" />
        )}
      </Card>
    </div>
  );
};

export default KPIPage;
