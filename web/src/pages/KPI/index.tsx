import React, { useState } from 'react';
import { Row, Col, Typography, Spin, Select, Card, Empty, Statistic } from 'antd';
import { useKPISummary, useKPITimeSeries } from '@/hooks/useKPI';
import KPICard from '@/components/KPICard';
import KPIChart from '@/components/KPIChart';
import { KPI_DEFINITIONS } from '@/utils/constants';

const { Title } = Typography;

const KPIPage: React.FC = () => {
  const [selectedMetric, setSelectedMetric] = useState(KPI_DEFINITIONS[0].name);
  const { data: summary, isLoading: summaryLoading } = useKPISummary();
  const { data: timeseries, isLoading: tsLoading } = useKPITimeSeries(selectedMetric);

  return (
    <div>
      <Title level={4} style={{ marginBottom: 16 }}>KPI Dashboard</Title>

      {summaryLoading ? (
        <Spin size="large" style={{ display: 'block', margin: '40px auto' }} />
      ) : summary ? (
        <Row gutter={[16, 16]} style={{ marginBottom: 24 }}>
          <Col xs={12} sm={8} md={6}>
            <Card size="small">
              <Statistic title="Total Tasks" value={summary.total_tasks} />
            </Card>
          </Col>
          <Col xs={12} sm={8} md={6}>
            <Card size="small">
              <Statistic title="Completed" value={summary.completed_tasks} />
            </Card>
          </Col>
          <Col xs={12} sm={8} md={6}>
            <Card size="small">
              <Statistic title="Success Rate" value={summary.success_rate} suffix="%" />
            </Card>
          </Col>
          <Col xs={12} sm={8} md={6}>
            <Card size="small">
              <Statistic title="Total Tokens" value={summary.total_tokens} />
            </Card>
          </Col>
          <Col xs={12} sm={8} md={6}>
            <Card size="small">
              <Statistic title="Total Cost" value={summary.total_cost_rmb} prefix="¥" precision={2} />
            </Card>
          </Col>
          <Col xs={12} sm={8} md={6}>
            <Card size="small">
              <Statistic title="Avg Duration" value={summary.avg_duration_minutes} suffix="min" precision={1} />
            </Card>
          </Col>
          {summary.metrics.length > 0 && summary.metrics.map((m) => (
            <Col key={m.metric_name} xs={12} sm={8} md={6}>
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
        ) : timeseries && timeseries.data.length > 0 ? (
          <KPIChart data={timeseries} height={400} />
        ) : (
          <Empty description="No time series data" />
        )}
      </Card>
    </div>
  );
};

export default KPIPage;
